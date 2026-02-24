#!/usr/bin/env python3
"""Memory Engine für ClawBot: disk-first STM + LTM + Cache."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class MemoryConfig:
    db_path: Path = Path("clawbot_memory.db")
    short_term_size: int = 6
    cache_ttl_seconds: int = 3600


class MemoryEngine:
    """Disk-first Speicher: keine dauerhafte RAM-Chat-History, alles in SQLite."""

    def __init__(self, config: Optional[MemoryConfig] = None) -> None:
        self.config = config or MemoryConfig()
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.row_factory = sqlite3.Row

    def init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_interactions_created_at
            ON interactions(created_at DESC, id DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                tag TEXT,
                priority INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_facts_priority_created
            ON facts(priority DESC, created_at DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                prompt_hash TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cache_expires
            ON cache(expires_at)
            """
        )
        self.conn.commit()

    def remember_turn(self, role: str, content: str) -> None:
        ts = int(time.time())
        self.conn.execute(
            "INSERT INTO interactions(role, content, created_at) VALUES (?, ?, ?)",
            (role, content, ts),
        )
        self.conn.commit()

    def recent_turns(self, limit: Optional[int] = None) -> List[str]:
        effective_limit = limit or self.config.short_term_size
        rows = self.conn.execute(
            """
            SELECT role, content FROM interactions
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (effective_limit,),
        ).fetchall()
        return [f"{r['role']}: {r['content']}" for r in reversed(rows)]

    def remember_fact(self, fact: str, tag: str = "general", priority: int = 1) -> None:
        self.conn.execute(
            "INSERT INTO facts(fact, tag, priority, created_at) VALUES (?, ?, ?, ?)",
            (fact, tag, priority, int(time.time())),
        )
        self.conn.commit()

    def top_facts(self, query: str = "", limit: int = 5) -> List[str]:
        if query:
            rows = self.conn.execute(
                """
                SELECT fact FROM facts
                WHERE fact LIKE ? OR tag LIKE ?
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT fact FROM facts
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [r["fact"] for r in rows]

    def build_context(self, query: str = "") -> str:
        stm = "\n".join(self.recent_turns())
        ltm = "\n".join([f"- {f}" for f in self.top_facts(query=query)])
        return f"Kurzzeitkontext:\n{stm}\n\nLangzeitwissen:\n{ltm}".strip()

    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        return hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()

    def get_cached(self, prompt: str) -> Optional[str]:
        prompt_hash = self._hash_prompt(prompt)
        row = self.conn.execute(
            "SELECT response, expires_at FROM cache WHERE prompt_hash = ?",
            (prompt_hash,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time()):
            self.conn.execute("DELETE FROM cache WHERE prompt_hash = ?", (prompt_hash,))
            self.conn.commit()
            return None
        return str(row["response"])

    def set_cached(self, prompt: str, response: str) -> None:
        now = int(time.time())
        expires = now + self.config.cache_ttl_seconds
        self.conn.execute(
            """
            INSERT INTO cache(prompt_hash, response, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(prompt_hash)
            DO UPDATE SET response=excluded.response, expires_at=excluded.expires_at
            """,
            (self._hash_prompt(prompt), response, expires, now),
        )
        self.conn.commit()

    def purge_expired_cache(self) -> int:
        cur = self.conn.execute("DELETE FROM cache WHERE expires_at < ?", (int(time.time()),))
        self.conn.commit()
        return int(cur.rowcount)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--purge-cache", action="store_true")
    args = parser.parse_args()

    engine = MemoryEngine()
    if args.init_db:
        engine.init_db()
        print("DB initialisiert.")
    if args.purge_cache:
        engine.init_db()
        deleted = engine.purge_expired_cache()
        print(f"Abgelaufene Cache-Einträge gelöscht: {deleted}")


if __name__ == "__main__":
    main()
