#!/usr/bin/env python3
"""
Telegram Network Transport Resilience & Failover.
Provides DoH-based IP discovery, Host/SNI preserving fallback transport, and TCP keepalive.
Adapted from hermes-agent/plugins/platforms/telegram/telegram_network.py.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Iterable, Optional, List, Dict, Any

import httpx
from telegram.request import HTTPXRequest

logger = logging.getLogger("antigravity-tele-bot.network")

_TELEGRAM_API_HOST = "api.telegram.org"
_TCP_KEEPALIVE_IDLE_S = 30
_TCP_KEEPALIVE_INTERVAL_S = 10
_TCP_KEEPALIVE_COUNT = 3

SEED_FALLBACK_IPS: List[str] = ["149.154.166.110", "149.154.167.220"]
_DOH_PROVIDERS: List[Dict[str, Any]] = [
    {
        "url": "https://dns.google/resolve",
        "params": {"name": _TELEGRAM_API_HOST, "type": "A"},
        "headers": {}
    },
    {
        "url": "https://cloudflare-dns.com/dns-query",
        "params": {"name": _TELEGRAM_API_HOST, "type": "A"},
        "headers": {"Accept": "application/dns-json"}
    },
]


def tcp_keepalive_socket_options() -> List[tuple[int, int, int]]:
    """Generates setsockopt tuples for SO_KEEPALIVE to prevent hung sockets on Linux/Windows."""
    options: List[tuple[int, int, int]] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)
    for opt, value in (
        (idle, _TCP_KEEPALIVE_IDLE_S),
        (getattr(socket, "TCP_KEEPINTVL", None), _TCP_KEEPALIVE_INTERVAL_S),
        (getattr(socket, "TCP_KEEPCNT", None), _TCP_KEEPALIVE_COUNT)
    ):
        if opt is not None:
            options.append((socket.IPPROTO_TCP, opt, value))
    return options


def _normalize_fallback_ips(ips: Iterable[str]) -> List[str]:
    valid: List[str] = []
    for candidate in ips:
        candidate_str = str(candidate).strip()
        try:
            addr = ipaddress.ip_address(candidate_str)
            if addr.version == 4 and not addr.is_private and not addr.is_loopback:
                valid.append(candidate_str)
        except ValueError:
            continue
    return valid


async def resolve_doh_ips(timeout: float = 4.0) -> List[str]:
    """Queries DoH providers (Google & Cloudflare) for Telegram Bot API IPv4 addresses."""
    discovered: List[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for provider in _DOH_PROVIDERS:
            try:
                resp = await client.get(
                    provider["url"],
                    params=provider["params"],
                    headers=provider["headers"]
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for answer in data.get("Answer", []):
                        if answer.get("type") == 1:  # Type A record
                            ip = answer.get("data", "").strip()
                            if ip:
                                discovered.append(ip)
            except Exception as e:
                logger.debug(f"DoH query to {provider['url']} failed: {e}")

    normalized = _normalize_fallback_ips(discovered + SEED_FALLBACK_IPS)
    return list(dict.fromkeys(normalized))


class TelegramFallbackTransport(httpx.AsyncBaseTransport):
    """
    HTTPX Async Transport that resolves api.telegram.org via IPv4 literals
    while preserving Host header and TLS SNI (like curl --resolve).
    Prevents IPv6 blackhole hanging in Linux VPS and Windows.
    """

    _POOL_LIMITS = httpx.Limits(max_connections=16, max_keepalive_connections=8)

    def __init__(self, fallback_ips: Optional[Iterable[str]] = None, **transport_kwargs):
        ips = list(fallback_ips) if fallback_ips else SEED_FALLBACK_IPS
        self._fallback_ips = list(dict.fromkeys(_normalize_fallback_ips(ips) + SEED_FALLBACK_IPS))
        transport_kwargs.setdefault("limits", self._POOL_LIMITS)
        transport_kwargs.setdefault("socket_options", tcp_keepalive_socket_options())
        self._transport_kwargs = transport_kwargs
        self._primary = httpx.AsyncHTTPTransport(**transport_kwargs)
        self._fallbacks: Dict[str, httpx.AsyncHTTPTransport] = {}
        self._fallback_lock = asyncio.Lock()
        self._sticky_ip: Optional[str] = None

    async def _get_fallback(self, ip: str) -> httpx.AsyncHTTPTransport:
        async with self._fallback_lock:
            transport = self._fallbacks.get(ip)
            if transport is None:
                transport = httpx.AsyncHTTPTransport(**self._transport_kwargs)
                self._fallbacks[ip] = transport
            return transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # If request is not targeting Telegram API, dispatch via primary transport
        if request.url.host != _TELEGRAM_API_HOST:
            return await self._primary.handle_async_request(request)

        # 1. Try sticky IP first if one succeeded previously
        if self._sticky_ip:
            try:
                cloned = self._rewrite_request(request, self._sticky_ip)
                transport = await self._get_fallback(self._sticky_ip)
                resp = await transport.handle_async_request(cloned)
                return resp
            except Exception as e:
                logger.debug(f"Sticky IP {self._sticky_ip} failed: {e}. Resetting sticky IP.")
                self._sticky_ip = None

        # 2. Try primary transport (standard DNS)
        try:
            return await self._primary.handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as e:
            logger.warning(f"Primary Telegram DNS connection failed ({e}). Trying fallback IPv4 endpoints...")

        # 3. Iterate through fallback IPs
        for ip in self._fallback_ips:
            try:
                cloned = self._rewrite_request(request, ip)
                transport = await self._get_fallback(ip)
                resp = await transport.handle_async_request(cloned)
                self._sticky_ip = ip
                logger.info(f"Connected to Telegram API via fallback IP: {ip}")
                return resp
            except Exception as e:
                logger.debug(f"Fallback endpoint {ip} failed: {e}")

        # If all fail, re-raise primary attempt
        return await self._primary.handle_async_request(request)

    def _rewrite_request(self, request: httpx.Request, ip: str) -> httpx.Request:
        new_url = request.url.copy_with(host=ip)
        headers = request.headers.copy()
        headers["Host"] = _TELEGRAM_API_HOST
        # Extensions for TLS SNI
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = _TELEGRAM_API_HOST
        return httpx.Request(
            method=request.method,
            url=new_url,
            headers=headers,
            content=request.stream,
            extensions=extensions
        )

    async def aclose(self) -> None:
        await self._primary.aclose()
        for t in self._fallbacks.values():
            await t.aclose()
        self._fallbacks.clear()


def build_resilient_request(
    proxy_url: Optional[str] = None,
    enable_fallback: bool = True
) -> HTTPXRequest:
    """Builds a robust PTB HTTPXRequest instance with keepalive and optional fallback transport."""
    socket_opts = tcp_keepalive_socket_options()
    httpx_kwargs: Dict[str, Any] = {}

    if enable_fallback and not proxy_url:
        transport = TelegramFallbackTransport(SEED_FALLBACK_IPS)
        httpx_kwargs["transport"] = transport

    return HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=15.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=5.0,
        socket_options=socket_opts,
        proxy=proxy_url if proxy_url else None,
        httpx_kwargs=httpx_kwargs if httpx_kwargs else None
    )
