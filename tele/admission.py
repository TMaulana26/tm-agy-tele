#!/usr/bin/env python3
"""
Telegram Update Admission Control & Anti-Replay Guard.
Deduplicates updates in-memory and via SQLite receipts to prevent duplicate execution
upon bot reconnection, restart, or polling timeout redelivery.
Adapted from hermes-agent/plugins/platforms/telegram/update_admission.py.
"""

from __future__ import annotations

import time
import logging
from collections import OrderedDict
from typing import Any, Optional, Dict
from telegram import Update
from telegram.ext import ContextTypes

from database.state import get_db

logger = logging.getLogger("antigravity-tele-bot.admission")

_SEEN_CAP = 4096
_seen_in_memory: OrderedDict[str, float] = OrderedDict()


def _bounded_put(cache: OrderedDict, key: str, val: float, cap: int = _SEEN_CAP) -> None:
    cache[key] = val
    cache.move_to_end(key)
    while len(cache) > cap:
        cache.popitem(last=False)


def is_update_admitted(bot_id: Any, update_id: Any) -> bool:
    """
    Checks if an update should be admitted for processing.
    Returns True if update is fresh and admitted.
    Returns False if update is a duplicate (dropped).
    """
    if update_id is None:
        return True

    key = f"{bot_id}:{update_id}"
    now = time.time()

    # 1. Fast in-memory check
    if key in _seen_in_memory:
        logger.debug(f"Dropping duplicate update {update_id} (found in memory cache)")
        return False

    # 2. Check SQLite persistent receipt
    db = get_db()
    if db.has_update_receipt(bot_id, update_id):
        _bounded_put(_seen_in_memory, key, now)
        logger.info(f"Dropping redelivered update {update_id} (found in persistent receipts)")
        return False

    # 3. Fresh update: record in both layers
    _bounded_put(_seen_in_memory, key, now)
    try:
        db.record_update_receipt(bot_id, update_id)
    except Exception as e:
        logger.warning(f"Failed to record update receipt in database: {e}")

    return True


def check_update_admission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Helper for PTB handler functions."""
    bot_id = getattr(context.bot, "id", "default_bot")
    update_id = getattr(update, "update_id", None)
    return is_update_admitted(bot_id, update_id)
