#!/usr/bin/env python3
"""
SQLite State storage for Antigravity Telegram Bot.
Handles DM topic bindings, root chat conversations, user model preferences,
and anti-replay update receipts.
"""

from __future__ import annotations

import os
import time
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import get_data_dir

from contextlib import contextmanager

logger = logging.getLogger("antigravity-tele-bot.database")

_DEFAULT_DB_FILE = Path(os.getenv("STATE_DB_PATH", "").strip() or get_data_dir() / "state.db")


class StateDatabase:
    """Manages persistent SQLite state for the bot."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or _DEFAULT_DB_FILE)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS telegram_dm_topic_bindings (
                    chat_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    topic_name TEXT,
                    workspace_path TEXT,
                    model_override TEXT,
                    system_prompt TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, thread_id)
                );

                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL DEFAULT 'root',
                    selected_model TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, chat_id, thread_id)
                );

                CREATE TABLE IF NOT EXISTS telegram_update_receipts (
                    bot_id TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (bot_id, update_id)
                );

                CREATE INDEX IF NOT EXISTS idx_update_receipts_created_at
                ON telegram_update_receipts(created_at);

                CREATE TABLE IF NOT EXISTS root_conversations (
                    user_id TEXT NOT NULL PRIMARY KEY,
                    conv_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)

            # WAL lets readers proceed while a write is in progress
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass

            # Dynamic column migration for existing databases
            for col, col_type in [
                ("workspace_path", "TEXT"),
                ("model_override", "TEXT"),
                ("system_prompt", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE telegram_dm_topic_bindings ADD COLUMN {col} {col_type};")
                except sqlite3.OperationalError:
                    pass

    # --------------------------------------------------------------------------
    # TOPIC BINDINGS & WORKSPACE ROUTING
    # --------------------------------------------------------------------------
    def get_topic_binding(self, chat_id: Any, thread_id: Any) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM telegram_dm_topic_bindings WHERE chat_id = ? AND thread_id = ?",
                (str(chat_id), str(thread_id))
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_topic_workspace(self, chat_id: Any, thread_id: Any) -> Optional[str]:
        """Returns custom workspace path for the given forum topic if configured."""
        binding = self.get_topic_binding(chat_id, thread_id)
        if binding and binding.get("workspace_path"):
            return binding["workspace_path"]
        return None

    def set_topic_binding(
        self,
        chat_id: Any,
        thread_id: Any,
        conv_id: str,
        topic_name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        model_override: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO telegram_dm_topic_bindings (
                    chat_id, thread_id, conv_id, topic_name, workspace_path, model_override, system_prompt, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                    conv_id = excluded.conv_id,
                    topic_name = COALESCE(excluded.topic_name, telegram_dm_topic_bindings.topic_name),
                    workspace_path = COALESCE(excluded.workspace_path, telegram_dm_topic_bindings.workspace_path),
                    model_override = COALESCE(excluded.model_override, telegram_dm_topic_bindings.model_override),
                    system_prompt = COALESCE(excluded.system_prompt, telegram_dm_topic_bindings.system_prompt),
                    updated_at = excluded.updated_at
            """, (str(chat_id), str(thread_id), conv_id, topic_name, workspace_path, model_override, system_prompt, now, now))

    def update_topic_name(self, chat_id: Any, thread_id: Any, topic_name: str) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE telegram_dm_topic_bindings
                SET topic_name = ?, updated_at = ?
                WHERE chat_id = ? AND thread_id = ?
            """, (topic_name, now, str(chat_id), str(thread_id)))

    def delete_topic_binding(self, chat_id: Any, thread_id: Any) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM telegram_dm_topic_bindings WHERE chat_id = ? AND thread_id = ?",
                (str(chat_id), str(thread_id))
            )

    def prune_stale_topic_binding(self, chat_id: Any, thread_id: Any) -> bool:
        """Prunes orphaned topic binding when thread was deleted or not found in Telegram (Hermes #31501)."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM telegram_dm_topic_bindings WHERE chat_id = ? AND thread_id = ?",
                (str(chat_id), str(thread_id))
            )
            return cur.rowcount > 0

    def list_topic_bindings(self, chat_id: Any) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM telegram_dm_topic_bindings WHERE chat_id = ? ORDER BY updated_at DESC",
                (str(chat_id),)
            )
            return [dict(r) for r in cur.fetchall()]

    # --------------------------------------------------------------------------
    # ROOT CHAT CONVERSATIONS (non-topic sessions survive restarts)
    # --------------------------------------------------------------------------
    def get_root_conversation(self, user_id: Any) -> Optional[str]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT conv_id FROM root_conversations WHERE user_id = ?",
                (str(user_id),)
            )
            row = cur.fetchone()
            return str(row["conv_id"]) if row and row["conv_id"] else None

    def set_root_conversation(self, user_id: Any, conv_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO root_conversations (user_id, conv_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    conv_id = excluded.conv_id,
                    updated_at = excluded.updated_at
            """, (str(user_id), conv_id, time.time()))

    def delete_root_conversation(self, user_id: Any) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM root_conversations WHERE user_id = ?", (str(user_id),))

    # --------------------------------------------------------------------------
    # MODEL PREFERENCES
    # --------------------------------------------------------------------------
    def get_user_model(self, user_id: Any, chat_id: Any, thread_id: Any = "root") -> Optional[str]:
        with self._get_connection() as conn:
            # 1. First check topic-specific preference if in a thread
            if str(thread_id) != "root":
                cur = conn.execute("""
                    SELECT selected_model FROM user_preferences
                    WHERE user_id = ? AND chat_id = ? AND thread_id = ?
                """, (str(user_id), str(chat_id), str(thread_id)))
                row = cur.fetchone()
                if row:
                    return str(row["selected_model"])

            # 2. Fall back to user's root preference
            cur = conn.execute("""
                SELECT selected_model FROM user_preferences
                WHERE user_id = ? AND chat_id = ? AND thread_id = 'root'
            """, (str(user_id), str(chat_id)))
            row = cur.fetchone()
            if row:
                return str(row["selected_model"])
        return None

    def set_user_model(self, user_id: Any, chat_id: Any, model: str, thread_id: Any = "root") -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO user_preferences (user_id, chat_id, thread_id, selected_model, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, chat_id, thread_id) DO UPDATE SET
                    selected_model = excluded.selected_model,
                    updated_at = excluded.updated_at
            """, (str(user_id), str(chat_id), str(thread_id), model, now))

    # --------------------------------------------------------------------------
    # ANTI-REPLAY RECEIPTS
    # --------------------------------------------------------------------------
    def has_update_receipt(self, bot_id: Any, update_id: Any) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT 1 FROM telegram_update_receipts WHERE bot_id = ? AND update_id = ?",
                (str(bot_id), str(update_id))
            )
            return cur.fetchone() is not None

    def record_update_receipt(self, bot_id: Any, update_id: Any) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO telegram_update_receipts (bot_id, update_id, created_at)
                VALUES (?, ?, ?)
            """, (str(bot_id), str(update_id), now))

    def prune_expired_receipts(self, max_age_seconds: float = 86400.0) -> int:
        cutoff = time.time() - max_age_seconds
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM telegram_update_receipts WHERE created_at < ?",
                (cutoff,)
            )
            return cur.rowcount


_db_singleton: Optional[StateDatabase] = None

def get_db(db_path: Optional[Path | str] = None) -> StateDatabase:
    """Returns singleton instance of StateDatabase."""
    global _db_singleton
    if _db_singleton is None or db_path is not None:
        _db_singleton = StateDatabase(db_path)
    return _db_singleton
