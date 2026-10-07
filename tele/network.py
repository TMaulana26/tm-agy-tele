#!/usr/bin/env python3
"""
Telegram Network Transport Resilience & Failover.
Provides DoH-based IP discovery, Host/SNI preserving fallback transport, and TCP keepalive.
Adapted from hermes-agent/plugins/platforms/telegram/telegram_network.py.
"""

from __future__ import annotations

import os
import sys
import re
import time
import asyncio
import ipaddress
import logging
import socket
from pathlib import Path
from typing import Iterable, Optional, List, Dict, Any, Callable, Coroutine

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


# ==============================================================================
# TOKEN & CREDENTIAL ERROR REDACTION (ALA HERMES)
# ==============================================================================
_TOKEN_PATTERN = re.compile(
    r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,50}\b"
)


def redact_telegram_error_text(error: Any) -> str:
    """
    Redacts sensitive bot tokens and API URLs from exceptions and log strings.
    Prevents token leakage into logs, terminal streams, or persistent status files.
    """
    if error is None:
        return ""
    text = str(error)
    if not text:
        return f"<{type(error).__name__}>"
    return re.sub(
        r"(bot)?[0-9]{8,12}:[A-Za-z0-9_-]{20,50}",
        lambda m: f"{m.group(1) or ''}[REDACTED_BOT_TOKEN]",
        text,
        flags=re.IGNORECASE
    )


# ==============================================================================
# SINGLE-INSTANCE PROCESS LOCK (ANTI-COLLISION / CONFLICT 409 GUARD)
# ==============================================================================
_LOCK_FILE: Optional[Path] = None
INSTANCE_LOCK_FILE: Optional[Path] = None


def acquire_instance_lock(lock_dir: Optional[Path] = None) -> bool:
    """
    Acquires single-instance lock file to prevent duplicate bot processes with the same token.
    Returns True if lock acquired, False if another active process holds the lock.
    """
    global _LOCK_FILE, INSTANCE_LOCK_FILE
    if INSTANCE_LOCK_FILE is not None:
        lock_file = Path(INSTANCE_LOCK_FILE)
    else:
        try:
            from config import get_data_dir
            target_dir = lock_dir or get_data_dir()
        except Exception:
            target_dir = Path.cwd() / ".telegram_state"
        target_dir.mkdir(parents=True, exist_ok=True)
        lock_file = target_dir / "bot.lock"
    _LOCK_FILE = lock_file

    if _LOCK_FILE.exists():
        try:
            old_pid = int(_LOCK_FILE.read_text(encoding="utf-8").strip())
            if _is_pid_alive(old_pid):
                logger.error(f"Another instance of antigravity-tele-bot is already running (PID {old_pid}).")
                return False
        except (ValueError, OSError):
            pass

    try:
        _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError as e:
        logger.error(f"Could not write instance lock file: {e}")
        return False


def release_instance_lock() -> None:
    """Releases the instance lock file on clean shutdown."""
    global _LOCK_FILE, INSTANCE_LOCK_FILE
    lock_file = INSTANCE_LOCK_FILE if INSTANCE_LOCK_FILE is not None else _LOCK_FILE
    if lock_file and lock_file.exists():
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass
    _LOCK_FILE = None


def _is_pid_alive(pid: int) -> bool:
    """Checks whether the given PID represents an active running process."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h_proc:
            kernel32.CloseHandle(h_proc)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# ==============================================================================
# POLLING STALL WATCHDOG (ALA HERMES)
# ==============================================================================
class PollingStallWatchdog:
    """
    Heartbeat and stall detector for Telegram Long Polling.
    Monitors update progress and probes getWebhookInfo.pending_update_count.
    Detects silent dead TCP/CLOSE-WAIT hangs and triggers soft recovery.
    """

    def __init__(
        self,
        stall_timeout: float = 120.0,
        probe_interval: float = 20.0,
        on_stall_callback: Optional[Callable[[], Coroutine[Any, Any, None]]] = None,
        timeout_seconds: Optional[float] = None
    ):
        if timeout_seconds is not None:
            stall_timeout = timeout_seconds
        self.stall_timeout = max(5.0, float(stall_timeout))
        self.probe_interval = max(1.0, float(probe_interval))
        self.on_stall_callback = on_stall_callback
        self.last_progress_monotonic = time.monotonic()
        self._task: Optional[asyncio.Task] = None
        self._stopped = False
        self._pending_stuck_count = 0

    def record_progress(self) -> None:
        """Called whenever an update is successfully admitted or processed."""
        self.last_progress_monotonic = time.monotonic()
        self._pending_stuck_count = 0

    def notify_progress(self) -> None:
        """Alias for record_progress."""
        self.record_progress()

    async def _loop(self, app: Any) -> None:
        logger.info("Telegram Polling Stall Watchdog started.")
        while not self._stopped:
            try:
                await asyncio.sleep(self.probe_interval)
                if self._stopped:
                    break

                now = time.monotonic()
                updater = getattr(app, "updater", None)
                if updater is None or not getattr(updater, "running", False):
                    continue

                bot = getattr(app, "bot", None)
                if bot and hasattr(bot, "get_webhook_info"):
                    try:
                        info = await asyncio.wait_for(bot.get_webhook_info(), timeout=8.0)
                        pending = getattr(info, "pending_update_count", 0)
                        if pending > 0:
                            if now - self.last_progress_monotonic > self.stall_timeout:
                                self._pending_stuck_count += 1
                                logger.warning(
                                    f"Polling stall detected: {pending} pending updates queued but no local progress for "
                                    f"{now - self.last_progress_monotonic:.1f}s (probe {self._pending_stuck_count}/2)"
                                )
                                if self._pending_stuck_count >= 2:
                                    logger.error("Polling stalled with queued updates! Triggering recovery.")
                                    self._pending_stuck_count = 0
                                    if self.on_stall_callback:
                                        await self.on_stall_callback()
                        else:
                            self._pending_stuck_count = 0
                    except Exception as e:
                        logger.debug(f"Watchdog probe exception: {redact_telegram_error_text(e)}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in PollingStallWatchdog loop: {redact_telegram_error_text(e)}")

    def start(self, app: Any) -> None:
        if self._task and not self._task.done():
            return
        self._stopped = False
        self.last_progress_monotonic = time.monotonic()
        self._task = asyncio.create_task(self._loop(app))

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
