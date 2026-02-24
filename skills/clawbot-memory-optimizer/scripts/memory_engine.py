#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional


@dataclass
class MemoryConfig:
    db_path: Path = Path("clawbot_memory.db")
    short_term_size: int = 6
    cache_ttl_seconds: int = 3600
    semantic_similarity_threshold: float = 0.90
    semantic_candidate_limit: int = 50
    summarizer_turn_threshold: int = 20


class MemoryEngine:
    def __init__(self, config: Optional[MemoryConfig] = None) -> None:
        self.config = config or MemoryConfig()
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.row_factory = sqlite3.Row

    def _ensure_column(self, table: str, column_def: str, column_name: str) -> None:
        cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column_name not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
            self.conn.commit()

    def init_db(self) -> None:
        c = self.conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                user_id TEXT NOT NULL DEFAULT 'anonymous',
                tenant_id TEXT NOT NULL DEFAULT 'default',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._ensure_column("interactions", "session_id TEXT NOT NULL DEFAULT 'default'", "session_id")
        self._ensure_column("interactions", "user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id")
        self._ensure_column("interactions", "tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_interactions_scope_time ON interactions(tenant_id, user_id, session_id, created_at DESC, id DESC)"
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                user_id TEXT NOT NULL DEFAULT 'anonymous',
                tenant_id TEXT NOT NULL DEFAULT 'default',
                fact TEXT NOT NULL,
                tag TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                source_turn_id INTEGER,
                expires_at INTEGER,
                priority INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._ensure_column("facts", "session_id TEXT NOT NULL DEFAULT 'default'", "session_id")
        self._ensure_column("facts", "user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id")
        self._ensure_column("facts", "tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id")
        self._ensure_column("facts", "confidence REAL NOT NULL DEFAULT 1.0", "confidence")
        self._ensure_column("facts", "source_turn_id INTEGER", "source_turn_id")
        self._ensure_column("facts", "expires_at INTEGER", "expires_at")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_scope_priority ON facts(tenant_id, user_id, session_id, priority DESC, created_at DESC)"
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                from_turn_id INTEGER NOT NULL,
                to_turn_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_summaries_scope_time ON summaries(tenant_id, user_id, session_id, created_at DESC)"
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS exact_cache_entries (
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                response TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, user_id, session_id, prompt_hash)
            )
            """
        )
        self._ensure_column("exact_cache_entries", "tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id")
        self._ensure_column("exact_cache_entries", "user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id")
        c.execute("CREATE INDEX IF NOT EXISTS idx_exact_cache_expires ON exact_cache_entries(expires_at)")

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_cache_entries (
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, user_id, session_id, prompt)
            )
            """
        )
        self._ensure_column("semantic_cache_entries", "tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id")
        self._ensure_column("semantic_cache_entries", "user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id")
        c.execute("CREATE INDEX IF NOT EXISTS idx_semantic_cache_expires ON semantic_cache_entries(expires_at)")

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                value REAL NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._ensure_column("metrics", "user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id")
        self._ensure_column("metrics", "tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_metrics_scope_metric_time ON metrics(tenant_id, user_id, session_id, metric_name, created_at DESC)"
        )
        self.conn.commit()

    def _scope(self, session_id: str, user_id: str, tenant_id: str) -> tuple[str, str, str]:
        return tenant_id, user_id, session_id

    def record_metric(self, session_id: str, metric_name: str, value: float = 1.0, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        self.conn.execute(
            "INSERT INTO metrics(session_id, user_id, tenant_id, metric_name, value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, metric_name, float(value), int(time.time())),
        )
        self.conn.commit()

    def metrics_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> dict:
        rows = self.conn.execute(
            "SELECT metric_name, SUM(value) AS total FROM metrics WHERE tenant_id = ? AND user_id = ? AND session_id = ? GROUP BY metric_name",
            (tenant_id, user_id, session_id),
        ).fetchall()
        return {str(r["metric_name"]): float(r["total"]) for r in rows}

    def remember_turn(self, session_id: str, role: str, content: str, user_id: str = "anonymous", tenant_id: str = "default") -> int:
        cur = self.conn.execute(
            "INSERT INTO interactions(session_id, user_id, tenant_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, role, content, int(time.time())),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def recent_turns(self, session_id: str, limit: Optional[int] = None, user_id: str = "anonymous", tenant_id: str = "default") -> List[str]:
        rows = self.conn.execute(
            "SELECT role, content FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (tenant_id, user_id, session_id, limit or self.config.short_term_size),
        ).fetchall()
        return [f"{r['role']}: {r['content']}" for r in reversed(rows)]

    def remember_fact(self, session_id: str, fact: str, tag: str = "general", priority: int = 1, confidence: float = 1.0, source_turn_id: Optional[int] = None, expires_at: Optional[int] = None, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        self.conn.execute(
            "INSERT INTO facts(session_id, user_id, tenant_id, fact, tag, confidence, source_turn_id, expires_at, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, fact, tag, confidence, source_turn_id, expires_at, priority, int(time.time())),
        )
        self.conn.commit()

    def top_facts(self, session_id: str, query: str = "", limit: int = 5, user_id: str = "anonymous", tenant_id: str = "default") -> List[str]:
        now = int(time.time())
        if query:
            rows = self.conn.execute(
                "SELECT fact FROM facts WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND (expires_at IS NULL OR expires_at > ?) AND (fact LIKE ? OR tag LIKE ?) ORDER BY priority DESC, confidence DESC, created_at DESC LIMIT ?",
                (tenant_id, user_id, session_id, now, f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT fact FROM facts WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND (expires_at IS NULL OR expires_at > ?) ORDER BY priority DESC, confidence DESC, created_at DESC LIMIT ?",
                (tenant_id, user_id, session_id, now, limit),
            ).fetchall()
        return [str(r["fact"]) for r in rows]

    def latest_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> Optional[str]:
        row = self.conn.execute(
            "SELECT summary FROM summaries WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT 1",
            (tenant_id, user_id, session_id),
        ).fetchone()
        return str(row["summary"]) if row else None

    def needs_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            (tenant_id, user_id, session_id),
        ).fetchone()
        return int(row["c"]) >= self.config.summarizer_turn_threshold

    def create_summary(self, session_id: str, summary: str, from_turn_id: int, to_turn_id: int, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        self.conn.execute(
            "INSERT INTO summaries(session_id, user_id, tenant_id, summary, from_turn_id, to_turn_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, summary, from_turn_id, to_turn_id, int(time.time())),
        )
        self.conn.commit()

    def build_context(self, session_id: str, query: str = "", user_id: str = "anonymous", tenant_id: str = "default") -> str:
        summary = self.latest_summary(session_id, user_id=user_id, tenant_id=tenant_id) or ""
        facts = self.top_facts(session_id, query=query, user_id=user_id, tenant_id=tenant_id)
        recent = self.recent_turns(session_id, user_id=user_id, tenant_id=tenant_id)
        summary_block = f"Zusammenfassung:\n{summary}" if summary else "Zusammenfassung:\n(nicht vorhanden)"
        facts_block = "Top Facts:\n" + "\n".join([f"- {f}" for f in facts])
        recent_block = "Letzte Turns:\n" + "\n".join(recent)
        return f"{summary_block}\n\n{facts_block}\n\n{recent_block}".strip()

    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        return hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return -1.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return -1.0
        return dot / (norm_a * norm_b)

    def get_cached(self, session_id: str, prompt: str, user_id: str = "anonymous", tenant_id: str = "default") -> Optional[str]:
        row = self.conn.execute(
            "SELECT response, expires_at FROM exact_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt_hash = ?",
            (*self._scope(session_id, user_id, tenant_id), self._hash_prompt(prompt)),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time()):
            self.conn.execute(
                "DELETE FROM exact_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt_hash = ?",
                (*self._scope(session_id, user_id, tenant_id), self._hash_prompt(prompt)),
            )
            self.conn.commit()
            return None
        return str(row["response"])

    def set_cached(self, session_id: str, prompt: str, response: str, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        now = int(time.time())
        scope = self._scope(session_id, user_id, tenant_id)
        h = self._hash_prompt(prompt)
        self.conn.execute(
            "DELETE FROM exact_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt_hash = ?",
            (*scope, h),
        )
        self.conn.execute(
            "INSERT INTO exact_cache_entries(tenant_id, user_id, session_id, prompt_hash, response, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*scope, h, response, now + self.config.cache_ttl_seconds, now),
        )
        self.conn.commit()

    def set_semantic_cached(self, session_id: str, prompt: str, response: str, embedding: List[float], user_id: str = "anonymous", tenant_id: str = "default") -> None:
        now = int(time.time())
        scope = self._scope(session_id, user_id, tenant_id)
        key_prompt = prompt.strip()
        self.conn.execute(
            "DELETE FROM semantic_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt = ?",
            (*scope, key_prompt),
        )
        self.conn.execute(
            "INSERT INTO semantic_cache_entries(tenant_id, user_id, session_id, prompt, response, embedding_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*scope, key_prompt, response, json.dumps(embedding), now + self.config.cache_ttl_seconds, now),
        )
        self.conn.commit()

    def get_semantic_cached(self, session_id: str, prompt: str, embedder: Callable[[str], List[float]], threshold: Optional[float] = None, candidate_limit: Optional[int] = None, user_id: str = "anonymous", tenant_id: str = "default") -> Optional[str]:
        now = int(time.time())
        self.conn.execute(
            "DELETE FROM semantic_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND expires_at < ?",
            (*self._scope(session_id, user_id, tenant_id), now),
        )
        self.conn.commit()
        query_embedding = embedder(prompt)
        if not query_embedding:
            return None
        rows = self.conn.execute(
            "SELECT response, embedding_json FROM semantic_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT ?",
            (*self._scope(session_id, user_id, tenant_id), candidate_limit or self.config.semantic_candidate_limit),
        ).fetchall()
        best_response = None
        best_sim = -1.0
        for row in rows:
            try:
                emb = json.loads(row["embedding_json"])
            except json.JSONDecodeError:
                continue
            sim = self._cosine_similarity(query_embedding, emb)
            if sim > best_sim:
                best_sim, best_response = sim, str(row["response"])
        if best_response is not None and best_sim >= (threshold if threshold is not None else self.config.semantic_similarity_threshold):
            return best_response
        return None

    def purge_expired_cache(self) -> int:
        now = int(time.time())
        exact = self.conn.execute("DELETE FROM exact_cache_entries WHERE expires_at < ?", (now,)).rowcount or 0
        semantic = self.conn.execute("DELETE FROM semantic_cache_entries WHERE expires_at < ?", (now,)).rowcount or 0
        self.conn.execute("DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        self.conn.commit()
        return int(exact + semantic)


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
        print(f"Abgelaufene Cache-Einträge gelöscht: {engine.purge_expired_cache()}")


if __name__ == "__main__":
    main()
