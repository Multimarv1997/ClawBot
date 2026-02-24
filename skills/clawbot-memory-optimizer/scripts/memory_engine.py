#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sqlite3
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class MemoryConfig:
    db_path: Path = Path("clawbot_memory.db")
    short_term_size: int = 6
    cache_ttl_seconds: int = 3600
    semantic_similarity_threshold: float = 0.90
    semantic_candidate_limit: int = 50
    summarizer_turn_threshold: int = 20
    stale_fact_days: int = 30
    unhit_fact_days: int = 7
    decay_lambda: float = 0.03
    archive_threshold: float = 0.05
    context_token_budget: int = 700
    context_block_budget_identity: int = 100
    context_block_budget_soul: int = 100
    context_block_budget_summary: int = 80
    context_block_budget_facts: int = 90
    context_block_budget_prev_facts: int = 50
    context_block_budget_trends: int = 40
    context_block_budget_graph_entities: int = 50
    context_block_budget_graph_relations: int = 50
    context_block_budget_recent_turns: int = 140
    # Phase D1
    reflection_baseline_tokens: int = 8000
    reflection_max_tokens: int = 16000
    reflection_elements_min: int = 5
    reflection_elements_max: int = 8
    reflection_log_limit: int = 10
    reflection_auto_approve_threshold: float = 0.9
    max_pending_proposals: int = 50
    proposal_auto_expire_hours: int = 24
    proposal_review_timeout_minutes: int = 30
    audit_retention_days: int = 90


TYPE_WEIGHTS = {
    "core": 1.5,
    "semantic": 1.2,
    "episodic": 0.8,
    "procedural": 1.0,
    "vault": 9999.0,
}


class MemoryEngine:
    def __init__(self, config: Optional[MemoryConfig] = None) -> None:
        self.config = config or MemoryConfig()
        self.conn = sqlite3.connect(self.config.db_path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._db_lock = threading.RLock()

    def _exec(self, sql: str, params: tuple = (), commit: bool = False):
        for i in range(3):
            try:
                with self._db_lock:
                    cur = self.conn.execute(sql, params)
                    if commit:
                        self.conn.commit()
                    return cur
            except sqlite3.OperationalError as err:
                if "locked" in str(err).lower() and i < 2:
                    time.sleep(0.05 * (i + 1))
                    continue
                logger.error("DB error: %s", err)
                raise
        raise RuntimeError("DB execution failed after retries")

    def _ensure_column(self, table: str, column_def: str, column_name: str) -> None:
        allowed_tables = {
            "interactions", "facts", "summaries", "metrics", "exact_cache_entries", "semantic_cache_entries",
            "entities", "relations", "identity", "soul", "summary_locks", "reflection_queue", "reflection_log",
            "agents", "memory_proposals", "audit_log",
        }
        if table not in allowed_tables:
            raise ValueError(f"unsupported table for schema migration: {table}")
        cols = [r[1] for r in self._exec(f"PRAGMA table_info({table})").fetchall()]
        if column_name not in cols:
            try:
                self._exec(f"ALTER TABLE {table} ADD COLUMN {column_def}", commit=True)
            except sqlite3.OperationalError as err:
                if "duplicate column name" not in str(err).lower():
                    raise

    def init_db(self) -> None:
        self._exec(
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
            """,
            commit=True,
        )
        for col_def, col_name in [
            ("session_id TEXT NOT NULL DEFAULT 'default'", "session_id"),
            ("user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id"),
            ("tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id"),
        ]:
            self._ensure_column("interactions", col_def, col_name)
        self._exec("CREATE INDEX IF NOT EXISTS idx_interactions_scope_time ON interactions(tenant_id, user_id, session_id, created_at DESC, id DESC)", commit=True)

        self._exec(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                user_id TEXT NOT NULL DEFAULT 'anonymous',
                tenant_id TEXT NOT NULL DEFAULT 'default',
                memory_scope TEXT NOT NULL DEFAULT 'session',
                memory_type TEXT NOT NULL DEFAULT 'semantic',
                memory_status TEXT NOT NULL DEFAULT 'active',
                relevance_score REAL NOT NULL DEFAULT 1.0,
                relevance_base REAL NOT NULL DEFAULT 1.0,
                fact_key TEXT,
                fact TEXT NOT NULL,
                tag TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                source_turn_id INTEGER,
                expires_at INTEGER,
                priority INTEGER DEFAULT 1,
                hit_count INTEGER NOT NULL DEFAULT 0,
                conflict_group TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_accessed_at INTEGER
            )
            """,
            commit=True,
        )
        for col_def, col_name in [
            ("session_id TEXT NOT NULL DEFAULT 'default'", "session_id"),
            ("user_id TEXT NOT NULL DEFAULT 'anonymous'", "user_id"),
            ("tenant_id TEXT NOT NULL DEFAULT 'default'", "tenant_id"),
            ("memory_scope TEXT NOT NULL DEFAULT 'session'", "memory_scope"),
            ("memory_type TEXT NOT NULL DEFAULT 'semantic'", "memory_type"),
            ("memory_status TEXT NOT NULL DEFAULT 'active'", "memory_status"),
            ("relevance_score REAL NOT NULL DEFAULT 1.0", "relevance_score"),
            ("relevance_base REAL NOT NULL DEFAULT 1.0", "relevance_base"),
            ("fact_key TEXT", "fact_key"),
            ("confidence REAL NOT NULL DEFAULT 1.0", "confidence"),
            ("source_turn_id INTEGER", "source_turn_id"),
            ("expires_at INTEGER", "expires_at"),
            ("hit_count INTEGER NOT NULL DEFAULT 0", "hit_count"),
            ("conflict_group TEXT", "conflict_group"),
            ("updated_at INTEGER NOT NULL DEFAULT 0", "updated_at"),
            ("last_accessed_at INTEGER", "last_accessed_at"),
        ]:
            self._ensure_column("facts", col_def, col_name)
        self._exec("CREATE INDEX IF NOT EXISTS idx_facts_scope_priority ON facts(tenant_id, user_id, session_id, memory_scope, memory_status, relevance_score DESC, priority DESC, confidence DESC, hit_count DESC, updated_at DESC)", commit=True)

        self._exec(
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
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_summaries_scope_time ON summaries(tenant_id, user_id, session_id, created_at DESC)", commit=True)

        self._exec(
            """
            CREATE TABLE IF NOT EXISTS summary_locks (
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                locked_at INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, user_id, session_id)
            )
            """,
            commit=True,
        )

        self._exec(
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
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_exact_cache_expires ON exact_cache_entries(expires_at)", commit=True)

        self._exec(
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
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_semantic_cache_expires ON semantic_cache_entries(expires_at)", commit=True)

        self._exec(
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
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_metrics_scope_metric_time ON metrics(tenant_id, user_id, session_id, metric_name, created_at DESC)", commit=True)

        # Phase B/C vorbereitet
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                entity_name TEXT NOT NULL,
                entity_type TEXT,
                description TEXT,
                confidence REAL DEFAULT 1.0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """,
            commit=True,
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                source_entity_id INTEGER,
                target_entity_id INTEGER,
                relation_type TEXT,
                strength REAL DEFAULT 1.0,
                created_at INTEGER NOT NULL
            )
            """,
            commit=True,
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS identity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                stability TEXT DEFAULT 'stable',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_identity_tenant_category_stability ON identity(tenant_id, category, stability, updated_at DESC)", commit=True)
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS soul (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_soul_tenant_category_time ON soul(tenant_id, category, updated_at DESC)", commit=True)

        # Phase D1: Reflection Queue
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS reflection_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL DEFAULT 'explicit',
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER DEFAULT 1,
                tokens_requested INTEGER DEFAULT 8000,
                token_reason TEXT,
                content_preview TEXT,
                reflection_text TEXT,
                elements_used TEXT,
                tokens_used INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                approved_at INTEGER,
                executed_at INTEGER,
                rejected_at INTEGER,
                rejection_reason TEXT
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_reflection_queue_status_time ON reflection_queue(tenant_id, user_id, status, created_at DESC)", commit=True)

        self._exec(
            """
            CREATE TABLE IF NOT EXISTS reflection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                reflection_text TEXT NOT NULL,
                elements_used TEXT,
                tokens_used INTEGER,
                memory_insights TEXT,
                created_at INTEGER NOT NULL
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_reflection_log_tenant_time ON reflection_log(tenant_id, user_id, created_at DESC)", commit=True)

        # Phase D1: Multi-Agent proposals (schema only)
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                agent_type TEXT NOT NULL DEFAULT 'subagent',
                permissions TEXT NOT NULL DEFAULT 'read',
                active INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL,
                last_active_at INTEGER,
                UNIQUE(tenant_id, agent_name)
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_agents_tenant_name ON agents(tenant_id, agent_name)", commit=True)

        self._exec(
            """
            CREATE TABLE IF NOT EXISTS memory_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                target_store TEXT NOT NULL,
                proposal_type TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence TEXT DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER DEFAULT 1,
                reviewed_by TEXT,
                review_comment TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                approved_at INTEGER,
                rejected_at INTEGER,
                rejection_reason TEXT,
                executed_at INTEGER
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_memory_proposals_status_time ON memory_proposals(tenant_id, status, created_at DESC)", commit=True)

        # Phase D1: Audit log
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_name TEXT,
                action_type TEXT NOT NULL,
                target_store TEXT,
                target_id INTEGER,
                details TEXT,
                created_at INTEGER NOT NULL
            )
            """,
            commit=True,
        )
        self._exec("CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_time ON audit_log(tenant_id, created_at DESC)", commit=True)

    def _scope(self, session_id: str, user_id: str, tenant_id: str) -> tuple[str, str, str]:
        return tenant_id, user_id, session_id

    @staticmethod
    def normalize_fact_key(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\säöüÄÖÜß]", "", text.lower())).strip()

    def _relevance(self, base: float, last_accessed: int, access_count: int, memory_type: str) -> float:
        if memory_type == "vault":
            return 1.0
        days = max(0.0, (time.time() - last_accessed) / 86400.0)
        weight = TYPE_WEIGHTS.get(memory_type, 1.0)
        return max(0.0, min(1.0, base * math.exp(-self.config.decay_lambda * days) * math.log2(access_count + 1) * weight))

    @staticmethod
    def _status(score: float) -> str:
        if score >= 0.5:
            return "active"
        if score >= 0.2:
            return "fading"
        if score >= 0.05:
            return "dormant"
        return "archived"

    def record_metric(self, session_id: str, metric_name: str, value: float = 1.0, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        self._exec(
            "INSERT INTO metrics(session_id, user_id, tenant_id, metric_name, value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, metric_name, float(value), int(time.time())),
            commit=True,
        )

    def metrics_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> dict:
        rows = self._exec(
            "SELECT metric_name, SUM(value) AS total FROM metrics WHERE tenant_id = ? AND user_id = ? AND session_id = ? GROUP BY metric_name",
            (tenant_id, user_id, session_id),
        ).fetchall()
        return {str(r["metric_name"]): float(r["total"]) for r in rows}

    def remember_turn(self, session_id: str, role: str, content: str, user_id: str = "anonymous", tenant_id: str = "default") -> int:
        cur = self._exec(
            "INSERT INTO interactions(session_id, user_id, tenant_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, role, content, int(time.time())),
            commit=True,
        )
        return int(cur.lastrowid)

    def recent_turns(self, session_id: str, limit: Optional[int] = None, user_id: str = "anonymous", tenant_id: str = "default") -> List[str]:
        rows = self._exec(
            "SELECT role, content FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (tenant_id, user_id, session_id, limit or self.config.short_term_size),
        ).fetchall()
        return [f"{r['role']}: {r['content']}" for r in reversed(rows)]

    def turns_since_last_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default", limit: int = 50):
        row = self._exec(
            "SELECT COALESCE(MAX(to_turn_id), 0) AS last_to FROM summaries WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            (tenant_id, user_id, session_id),
        ).fetchone()
        last_to = int(row["last_to"]) if row else 0
        return self._exec(
            "SELECT id, role, content FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (tenant_id, user_id, session_id, last_to, limit),
        ).fetchall()

    def try_acquire_summary_lock(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default", ttl_s: int = 120) -> bool:
        now = int(time.time())
        self._exec("DELETE FROM summary_locks WHERE locked_at < ?", (now - ttl_s,), commit=True)
        try:
            self._exec(
                "INSERT INTO summary_locks(tenant_id, user_id, session_id, locked_at) VALUES (?, ?, ?, ?)",
                (*self._scope(session_id, user_id, tenant_id), now),
                commit=True,
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_summary_lock(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        self._exec(
            "DELETE FROM summary_locks WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            self._scope(session_id, user_id, tenant_id),
            commit=True,
        )

    def remember_fact(self, session_id: str, fact: str, tag: str = "general", priority: int = 1, confidence: float = 1.0, source_turn_id: Optional[int] = None, expires_at: Optional[int] = None, memory_scope: str = "session", memory_type: str = "semantic", user_id: str = "anonymous", tenant_id: str = "default") -> None:
        now = int(time.time())
        fact_key = self.normalize_fact_key(fact)
        existing = self._exec(
            "SELECT id, confidence, priority, hit_count, fact, relevance_score FROM facts WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND memory_scope = ? AND fact_key = ? LIMIT 1",
            (tenant_id, user_id, session_id, memory_scope, fact_key),
        ).fetchone()

        if existing:
            new_conf = max(float(existing["confidence"]), float(confidence))
            new_prio = max(int(existing["priority"]), int(priority))
            new_rel = max(float(existing["relevance_score"]), 0.6)
            conflict_group = None
            old_fact = str(existing["fact"]).lower()
            new_fact = fact.lower()
            neg_patterns = [
                re.compile(r"\bnicht\b", re.IGNORECASE),
                re.compile(r"\bnot\b", re.IGNORECASE),
                re.compile(r"\bnever\b", re.IGNORECASE),
                re.compile(r"\bkein(?:e|er|em|en)?\b", re.IGNORECASE),
                re.compile(r"\bno\b", re.IGNORECASE),
            ]
            old_neg = any(p.search(old_fact) for p in neg_patterns)
            new_neg = any(p.search(new_fact) for p in neg_patterns)
            if old_neg != new_neg:
                conflict_group = fact_key
            self._exec(
                "UPDATE facts SET fact = ?, tag = ?, confidence = ?, priority = ?, hit_count = hit_count + 1, relevance_score = ?, relevance_base = MAX(COALESCE(relevance_base, 1.0), ?), memory_status = ?, source_turn_id = COALESCE(?, source_turn_id), expires_at = COALESCE(?, expires_at), conflict_group = COALESCE(?, conflict_group), updated_at = ?, last_accessed_at = ? WHERE id = ?",
                (fact, tag, new_conf, new_prio, new_rel, float(confidence), self._status(new_rel), source_turn_id, expires_at, conflict_group, now, now, int(existing["id"])),
                commit=True,
            )
            return

        self._exec(
            "INSERT INTO facts(session_id, user_id, tenant_id, memory_scope, memory_type, memory_status, relevance_score, relevance_base, fact_key, fact, tag, confidence, source_turn_id, expires_at, priority, hit_count, conflict_group, created_at, updated_at, last_accessed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, memory_scope, memory_type, "active", 1.0, 1.0, fact_key, fact, tag, confidence, source_turn_id, expires_at, priority, 0, None, now, now, now),
            commit=True,
        )

    def forget_facts(self, session_id: str, pattern: str, user_id: str = "anonymous", tenant_id: str = "default", include_user_tenant_scopes: bool = True) -> int:
        q = f"%{pattern}%"
        scope_clause = "(session_id = ? OR memory_scope IN ('user','tenant'))" if include_user_tenant_scopes else "session_id = ?"
        deleted = self._exec(
            "DELETE FROM facts WHERE tenant_id = ? AND user_id = ? AND " + scope_clause + " AND (fact LIKE ? OR tag LIKE ?)",
            (tenant_id, user_id, session_id, q, q),
            commit=True,
        ).rowcount or 0
        return int(deleted)

    def top_facts(self, session_id: str, query: str = "", limit: int = 5, user_id: str = "anonymous", tenant_id: str = "default") -> List[str]:
        now = int(time.time())
        q = f"%{query}%"
        rows = self._exec(
            """
            SELECT id, fact, relevance_score, COALESCE(relevance_base, 1.0) AS relevance_base, hit_count, memory_type, COALESCE(last_accessed_at, created_at) AS last_accessed
            FROM facts
            WHERE tenant_id = ? AND user_id = ?
              AND (session_id = ? OR memory_scope IN ('user','tenant'))
              AND (expires_at IS NULL OR expires_at > ?)
              AND memory_status != 'archived'
              AND (? = '' OR fact LIKE ? OR tag LIKE ?)
            ORDER BY
              CASE memory_scope WHEN 'session' THEN 0 WHEN 'user' THEN 1 ELSE 2 END,
              relevance_score DESC, priority DESC, confidence DESC, hit_count DESC, updated_at DESC
            LIMIT ?
            """,
            (tenant_id, user_id, session_id, now, query, q, q, limit),
        ).fetchall()

        return [str(r["fact"]) for r in rows]

    def previous_session_facts(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default", limit: int = 3) -> List[str]:
        current_latest = self._exec(
            "SELECT COALESCE(MAX(created_at), 0) AS ts FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            (tenant_id, user_id, session_id),
        ).fetchone()
        current_ts = int(current_latest["ts"] or 0)
        row = self._exec(
            """
            SELECT session_id, MAX(created_at) AS last_ts
            FROM interactions
            WHERE tenant_id = ? AND user_id = ? AND session_id != ?
            GROUP BY session_id
            HAVING (? = 0 OR MAX(created_at) <= ?)
            ORDER BY last_ts DESC
            LIMIT 1
            """,
            (tenant_id, user_id, session_id, current_ts, current_ts),
        ).fetchone()
        if not row:
            row = self._exec(
                "SELECT session_id FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id != ? GROUP BY session_id ORDER BY MAX(created_at) DESC LIMIT 1",
                (tenant_id, user_id, session_id),
            ).fetchone()
        if not row:
            return []
        prev_session = str(row["session_id"])
        rows = self._exec(
            "SELECT id, fact, relevance_score, COALESCE(relevance_base, 1.0) AS relevance_base, hit_count, memory_type, COALESCE(last_accessed_at, created_at) AS last_accessed FROM facts WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY relevance_score DESC, updated_at DESC LIMIT ?",
            (tenant_id, user_id, prev_session, limit),
        ).fetchall()
        return [str(r["fact"]) for r in rows]

    def tenant_topic_trends(self, tenant_id: str, limit: int = 3) -> List[str]:
        rows = self._exec(
            "SELECT metric_name, SUM(value) AS total FROM metrics WHERE tenant_id = ? AND metric_name LIKE 'topic_%' GROUP BY metric_name ORDER BY total DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
        return [f"{str(r['metric_name']).replace('topic_', '')}: {int(float(r['total']))}" for r in rows]


    def upsert_entity(self, tenant_id: str, user_id: str, entity_name: str, entity_type: str = "concept", description: str = "", confidence: float = 0.7) -> int:
        now = int(time.time())
        key = entity_name.strip().lower()
        row = self._exec(
            "SELECT id, confidence FROM entities WHERE tenant_id = ? AND user_id = ? AND LOWER(entity_name) = ? LIMIT 1",
            (tenant_id, user_id, key),
        ).fetchone()
        if row:
            self._exec(
                "UPDATE entities SET entity_type = ?, description = CASE WHEN ? != '' THEN ? ELSE description END, confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?",
                (entity_type, description, description, confidence, now, int(row["id"])),
                commit=True,
            )
            return int(row["id"])
        cur = self._exec(
            "INSERT INTO entities(tenant_id, user_id, entity_name, entity_type, description, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, user_id, entity_name.strip(), entity_type, description, confidence, now, now),
            commit=True,
        )
        return int(cur.lastrowid)

    def upsert_relation(self, tenant_id: str, user_id: str, source_entity_id: int, target_entity_id: int, relation_type: str = "related_to", strength: float = 0.7) -> None:
        row = self._exec(
            "SELECT id, strength FROM relations WHERE tenant_id = ? AND user_id = ? AND source_entity_id = ? AND target_entity_id = ? AND relation_type = ? LIMIT 1",
            (tenant_id, user_id, source_entity_id, target_entity_id, relation_type),
        ).fetchone()
        now = int(time.time())
        if row:
            self._exec(
                "UPDATE relations SET strength = MAX(strength, ?) WHERE id = ?",
                (strength, int(row["id"])),
                commit=True,
            )
            return
        self._exec(
            "INSERT INTO relations(tenant_id, user_id, source_entity_id, target_entity_id, relation_type, strength, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, user_id, source_entity_id, target_entity_id, relation_type, strength, now),
            commit=True,
        )

    def graph_top_entities(self, tenant_id: str, user_id: str, limit: int = 5) -> List[str]:
        rows = self._exec(
            "SELECT entity_name, confidence FROM entities WHERE tenant_id = ? AND user_id = ? ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (tenant_id, user_id, limit),
        ).fetchall()
        return [f"{r['entity_name']} ({float(r['confidence']):.2f})" for r in rows]

    def graph_top_relations(self, tenant_id: str, user_id: str, limit: int = 5) -> List[str]:
        rows = self._exec(
            """
            SELECT e1.entity_name AS src, e2.entity_name AS dst, r.relation_type AS rel, r.strength AS st
            FROM relations r
            JOIN entities e1 ON e1.id = r.source_entity_id
            JOIN entities e2 ON e2.id = r.target_entity_id
            WHERE r.tenant_id = ? AND r.user_id = ?
            ORDER BY r.strength DESC, r.created_at DESC
            LIMIT ?
            """,
            (tenant_id, user_id, limit),
        ).fetchall()
        return [f"{r['src']} -[{r['rel']}]-> {r['dst']} ({float(r['st']):.2f})" for r in rows]

    def latest_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> Optional[str]:
        row = self._exec(
            "SELECT summary FROM summaries WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT 1",
            (tenant_id, user_id, session_id),
        ).fetchone()
        return str(row["summary"]) if row else None

    def needs_summary(self, session_id: str, user_id: str = "anonymous", tenant_id: str = "default") -> bool:
        row = self._exec(
            "SELECT COALESCE(MAX(to_turn_id), 0) AS last_to FROM summaries WHERE tenant_id = ? AND user_id = ? AND session_id = ?",
            (tenant_id, user_id, session_id),
        ).fetchone()
        last_to = int(row["last_to"]) if row else 0
        row = self._exec(
            "SELECT COUNT(*) AS c FROM interactions WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND id > ?",
            (tenant_id, user_id, session_id, last_to),
        ).fetchone()
        return int(row["c"]) >= self.config.summarizer_turn_threshold

    def create_summary(self, session_id: str, summary: str, from_turn_id: int, to_turn_id: int, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        self._exec(
            "INSERT INTO summaries(session_id, user_id, tenant_id, summary, from_turn_id, to_turn_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, tenant_id, summary, from_turn_id, to_turn_id, int(time.time())),
            commit=True,
        )

    def build_context(self, session_id: str, query: str = "", user_id: str = "anonymous", tenant_id: str = "default", include_reflection_queue: bool = False) -> str:
        summary = self.latest_summary(session_id, user_id=user_id, tenant_id=tenant_id) or ""
        identity_facts = self.identity_context(tenant_id=tenant_id, max_tokens=self.config.context_block_budget_identity)
        soul_values = self.soul_context(tenant_id=tenant_id, max_tokens=self.config.context_block_budget_soul)
        facts = self.top_facts(session_id, query=query, user_id=user_id, tenant_id=tenant_id)
        prev = self.previous_session_facts(session_id, user_id=user_id, tenant_id=tenant_id)
        trends = self.tenant_topic_trends(tenant_id=tenant_id)
        entities = self.graph_top_entities(tenant_id=tenant_id, user_id=user_id, limit=4)
        relations = self.graph_top_relations(tenant_id=tenant_id, user_id=user_id, limit=4)
        recent = self.recent_turns(session_id, user_id=user_id, tenant_id=tenant_id)

        blocks = [
            self._format_block("Identity (stabil)", identity_facts, self.config.context_block_budget_identity),
            self._format_block("Soul (Werte/Prinzipien)", soul_values, self.config.context_block_budget_soul),
            self._format_block("Zusammenfassung", [summary] if summary else ["(nicht vorhanden)"], self.config.context_block_budget_summary),
            self._format_block("Top Facts", facts if facts else ["(keine)"], self.config.context_block_budget_facts),
            self._format_block("Facts letzte Sitzung", prev if prev else ["(keine)"], self.config.context_block_budget_prev_facts),
            self._format_block("Tenant Trends", trends if trends else ["(keine)"], self.config.context_block_budget_trends),
            self._format_block("Graph Entitäten", entities if entities else ["(keine)"], self.config.context_block_budget_graph_entities),
            self._format_block("Graph Relationen", relations if relations else ["(keine)"], self.config.context_block_budget_graph_relations),
            self._format_block("Letzte Turns", recent if recent else ["(keine)"], self.config.context_block_budget_recent_turns),
        ]
        if include_reflection_queue:
            pending_reflection = self.get_pending_reflection(tenant_id=tenant_id, user_id=user_id)
            if pending_reflection:
                blocks.append(self._format_block("Ausstehende Reflexion", [f"id={pending_reflection['id']} tokens={pending_reflection['tokens_requested']}"], 40))
            pending_props = self.get_pending_proposals(tenant_id=tenant_id, limit=3)
            if pending_props:
                prop_lines = [f"[{p['agent_name']}] {p['target_store']}:{p['proposal_type']}" for p in pending_props]
                blocks.append(self._format_block("Ausstehende Proposals", prop_lines, 50))
        return self._cap_global_budget("\n\n".join(blocks), self.config.context_token_budget)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 4))

    def _fit_lines_to_budget(self, lines: List[str], max_tokens: int) -> List[str]:
        out: List[str] = []
        used = 0
        for line in lines:
            line_tokens = self._estimate_tokens(line)
            if line_tokens <= 0:
                continue
            if used + line_tokens <= max_tokens:
                out.append(line)
                used += line_tokens
                continue
            remaining = max_tokens - used
            if remaining <= 0:
                break
            approx_chars = max(8, remaining * 4)
            out.append(line[: approx_chars - 1].rstrip() + "…")
            used = max_tokens
            break
        return out

    def _format_block(self, title: str, lines: List[str], max_tokens: int) -> str:
        fitted = self._fit_lines_to_budget([f"- {l}" for l in lines], max_tokens=max_tokens)
        body = "\n".join(fitted) if fitted else "- (leer)"
        return f"{title}:\n{body}"

    def _cap_global_budget(self, text: str, max_tokens: int) -> str:
        if self._estimate_tokens(text) <= max_tokens:
            return text
        approx_chars = max_tokens * 4
        return text[: approx_chars - 1].rstrip() + "…"

    def set_identity(self, tenant_id: str, category: str, content: str, stability: str = "stable") -> None:
        now = int(time.time())
        st = "dynamic" if stability == "dynamic" else "stable"
        row = self._exec(
            "SELECT id FROM identity WHERE tenant_id = ? AND category = ? AND content = ? LIMIT 1",
            (tenant_id, category, content),
        ).fetchone()
        if row:
            self._exec("UPDATE identity SET updated_at = ?, stability = ? WHERE id = ?", (now, st, int(row["id"])), commit=True)
            return
        self._exec(
            "INSERT INTO identity(tenant_id, category, content, stability, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, category, content, st, now, now),
            commit=True,
        )

    def set_soul(self, tenant_id: str, category: str, content: str) -> None:
        now = int(time.time())
        row = self._exec(
            "SELECT id FROM soul WHERE tenant_id = ? AND category = ? AND content = ? LIMIT 1",
            (tenant_id, category, content),
        ).fetchone()
        if row:
            self._exec("UPDATE soul SET updated_at = ? WHERE id = ?", (now, int(row["id"])), commit=True)
            return
        self._exec(
            "INSERT INTO soul(tenant_id, category, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (tenant_id, category, content, now, now),
            commit=True,
        )

    def identity_context(self, tenant_id: str, max_tokens: int = 120) -> List[str]:
        rows = self._exec(
            "SELECT category, content, stability FROM identity WHERE tenant_id = ? ORDER BY CASE stability WHEN 'stable' THEN 0 ELSE 1 END, updated_at DESC LIMIT 10",
            (tenant_id,),
        ).fetchall()
        rendered = [f"[{r['stability']}] {r['category']}: {r['content']}" for r in rows]
        return self._fit_lines_to_budget(rendered, max_tokens=max_tokens)

    def soul_context(self, tenant_id: str, max_tokens: int = 120) -> List[str]:
        rows = self._exec(
            "SELECT category, content FROM soul WHERE tenant_id = ? ORDER BY updated_at DESC LIMIT 10",
            (tenant_id,),
        ).fetchall()
        rendered = [f"{r['category']}: {r['content']}" for r in rows]
        return self._fit_lines_to_budget(rendered, max_tokens=max_tokens)

    def _log_audit(self, tenant_id: str, user_id: str, action_type: str, target_store: str, target_id: Optional[int], details: dict) -> None:
        now = int(time.time())
        actor_type = "user"
        actor_name = user_id
        if user_id in {"system", "reflection_queue", "memory_proposals"}:
            actor_type = "system"
        elif user_id.startswith("agent:"):
            actor_type = "subagent"
            actor_name = user_id.replace("agent:", "", 1)
        self._exec(
            "INSERT INTO audit_log(tenant_id, user_id, actor_type, actor_name, action_type, target_store, target_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, user_id, actor_type, actor_name, action_type, target_store, target_id, json.dumps(details, ensure_ascii=False), now),
            commit=True,
        )

    def get_audit_log(self, tenant_id: str, action_type: Optional[str] = None, target_store: Optional[str] = None, limit: int = 100) -> List[dict]:
        query = "SELECT id, actor_type, actor_name, action_type, target_store, target_id, details, created_at FROM audit_log WHERE tenant_id = ?"
        params: list = [tenant_id]
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if target_store:
            query += " AND target_store = ?"
            params.append(target_store)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._exec(query, tuple(params)).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": int(r["id"]),
                "actor_type": str(r["actor_type"]),
                "actor_name": str(r["actor_name"]) if r["actor_name"] else "",
                "action_type": str(r["action_type"]),
                "target_store": str(r["target_store"]) if r["target_store"] else "",
                "target_id": int(r["target_id"]) if r["target_id"] is not None else None,
                "details": {},
                "created_at": int(r["created_at"]),
            })
            if r["details"]:
                try:
                    out[-1]["details"] = json.loads(r["details"])
                except json.JSONDecodeError:
                    out[-1]["details"] = {"_raw": str(r["details"]), "_decode_error": True}
        return out

    def cleanup_old_audit_logs(self, retention_days: Optional[int] = None, tenant_id: Optional[str] = None) -> int:
        days = retention_days if retention_days is not None else self.config.audit_retention_days
        cutoff = int(time.time()) - days * 24 * 3600
        if tenant_id is None:
            deleted = self._exec("DELETE FROM audit_log WHERE created_at < ?", (cutoff,), commit=True).rowcount or 0
        else:
            deleted = self._exec("DELETE FROM audit_log WHERE tenant_id = ? AND created_at < ?", (tenant_id, cutoff), commit=True).rowcount or 0
        return int(deleted)

    def cleanup_stale_phase_d_state(self, tenant_id: Optional[str] = None) -> dict:
        now = int(time.time())
        proposal_cutoff = now - self.config.proposal_auto_expire_hours * 3600
        tenant_filter = " AND tenant_id = ?" if tenant_id is not None else ""
        tenant_params: tuple = (tenant_id,) if tenant_id is not None else ()
        expired_rows = self._exec(
            "SELECT id, tenant_id, agent_name, target_store, proposal_type FROM memory_proposals WHERE status = 'pending' AND created_at < ?" + tenant_filter,
            (proposal_cutoff, *tenant_params),
        ).fetchall()
        expired_proposals = self._exec(
            "UPDATE memory_proposals SET status = 'rejected', rejection_reason = COALESCE(rejection_reason, 'auto_expired'), rejected_at = ?, updated_at = ? WHERE status = 'pending' AND created_at < ?" + tenant_filter,
            (now, now, proposal_cutoff, *tenant_params),
            commit=True,
        ).rowcount or 0
        for row in expired_rows:
            self._log_audit(
                tenant_id=str(row["tenant_id"]),
                user_id="system",
                action_type="proposal_reject_auto",
                target_store=str(row["target_store"] or "memory_proposals"),
                target_id=int(row["id"]),
                details={
                    "original_agent": str(row["agent_name"]),
                    "proposal_type": str(row["proposal_type"]),
                    "reason": "auto_expired",
                },
            )
        old_reflections = self._exec(
            "DELETE FROM reflection_queue WHERE status IN ('executed', 'rejected') AND updated_at < ?" + tenant_filter,
            (proposal_cutoff, *tenant_params),
            commit=True,
        ).rowcount or 0
        old_audit = self.cleanup_old_audit_logs(tenant_id=tenant_id)
        return {"expired_proposals": int(expired_proposals), "deleted_reflection_queue_rows": int(old_reflections), "deleted_audit_rows": int(old_audit)}

    def phase_d_health(self, tenant_id: str, user_id: str) -> dict:
        pending_reflection = self._exec(
            "SELECT COUNT(*) AS c FROM reflection_queue WHERE tenant_id = ? AND user_id = ? AND status = 'pending'",
            (tenant_id, user_id),
        ).fetchone()
        pending_props = self._exec(
            "SELECT COUNT(*) AS c FROM memory_proposals WHERE tenant_id = ? AND status = 'pending'",
            (tenant_id,),
        ).fetchone()
        approved_props = self._exec(
            "SELECT COUNT(*) AS c FROM memory_proposals WHERE tenant_id = ? AND status = 'approved'",
            (tenant_id,),
        ).fetchone()
        executed_props = self._exec(
            "SELECT COUNT(*) AS c FROM memory_proposals WHERE tenant_id = ? AND status = 'executed'",
            (tenant_id,),
        ).fetchone()
        return {
            "pending_reflections": int(pending_reflection["c"]),
            "pending_proposals": int(pending_props["c"]),
            "approved_proposals": int(approved_props["c"]),
            "executed_proposals": int(executed_props["c"]),
        }

    def request_reflection(self, tenant_id: str, user_id: str, trigger_type: str = "explicit", token_reason: str = "", priority: int = 1) -> int:
        now = int(time.time())
        existing = self._exec(
            "SELECT id FROM reflection_queue WHERE tenant_id = ? AND user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
            (tenant_id, user_id),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = self._exec(
            "INSERT INTO reflection_queue(tenant_id, user_id, trigger_type, status, priority, tokens_requested, token_reason, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (tenant_id, user_id, trigger_type, max(1, min(5, int(priority))), self.config.reflection_baseline_tokens, token_reason[:240], now, now),
            commit=True,
        )
        rid = int(cur.lastrowid)
        self._log_audit(tenant_id, user_id, "reflection_request", "reflection_queue", rid, {"trigger_type": trigger_type, "tokens_requested": self.config.reflection_baseline_tokens})
        return rid

    def get_pending_reflection(self, tenant_id: str, user_id: str) -> Optional[dict]:
        row = self._exec(
            "SELECT id, trigger_type, status, tokens_requested, token_reason, content_preview, priority, created_at FROM reflection_queue WHERE tenant_id = ? AND user_id = ? AND status = 'pending' ORDER BY priority DESC, created_at ASC LIMIT 1",
            (tenant_id, user_id),
        ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "trigger_type": str(row["trigger_type"]),
            "status": str(row["status"]),
            "tokens_requested": int(row["tokens_requested"]),
            "token_reason": str(row["token_reason"] or ""),
            "content_preview": str(row["content_preview"] or ""),
            "priority": int(row["priority"]),
            "created_at": int(row["created_at"]),
        }

    def approve_reflection(self, reflection_id: int, tenant_id: str, user_id: str, approved_tokens: Optional[int] = None, custom_elements: Optional[List[str]] = None) -> bool:
        now = int(time.time())
        tokens = int(approved_tokens if approved_tokens is not None else self.config.reflection_baseline_tokens)
        tokens = max(256, min(tokens, self.config.reflection_max_tokens))
        if custom_elements is None:
            custom_elements = ["highlights", "insights", "learnings", "questions", "next_steps"]
        cur = self._exec(
            "UPDATE reflection_queue SET status = 'approved', approved_at = ?, updated_at = ?, tokens_used = ?, elements_used = ? WHERE id = ? AND tenant_id = ? AND user_id = ? AND status = 'pending'",
            (now, now, tokens, json.dumps(custom_elements, ensure_ascii=False), reflection_id, tenant_id, user_id),
            commit=True,
        )
        ok = (cur.rowcount or 0) > 0
        if ok:
            self._log_audit(tenant_id, user_id, "reflection_approve", "reflection_queue", reflection_id, {"approved_tokens": tokens, "elements": custom_elements})
        return bool(ok)

    def reject_reflection(self, reflection_id: int, tenant_id: str, user_id: str, rejection_reason: str = "") -> bool:
        now = int(time.time())
        cur = self._exec(
            "UPDATE reflection_queue SET status = 'rejected', rejected_at = ?, updated_at = ?, rejection_reason = ? WHERE id = ? AND tenant_id = ? AND user_id = ? AND status IN ('pending','approved')",
            (now, now, rejection_reason[:240], reflection_id, tenant_id, user_id),
            commit=True,
        )
        ok = (cur.rowcount or 0) > 0
        if ok:
            self._log_audit(tenant_id, user_id, "reflection_reject", "reflection_queue", reflection_id, {"reason": rejection_reason[:240]})
        return bool(ok)

    def execute_reflection(self, reflection_id: int, tenant_id: str, user_id: str, reflection_text: str) -> bool:
        text = reflection_text.strip()
        if not text:
            return False
        row = self._exec(
            "SELECT tokens_used, elements_used FROM reflection_queue WHERE id = ? AND tenant_id = ? AND user_id = ? AND status = 'approved'",
            (reflection_id, tenant_id, user_id),
        ).fetchone()
        if not row:
            return False
        now = int(time.time())
        tokens_used = int(row["tokens_used"] or self.config.reflection_baseline_tokens)
        elements = row["elements_used"] or "[]"
        cur = self._exec(
            "UPDATE reflection_queue SET status = 'executed', executed_at = ?, updated_at = ?, reflection_text = ? WHERE id = ? AND tenant_id = ? AND user_id = ? AND status = 'approved'",
            (now, now, text[:4000], reflection_id, tenant_id, user_id),
            commit=True,
        )
        if (cur.rowcount or 0) <= 0:
            return False
        self._exec(
            "INSERT INTO reflection_log(tenant_id, user_id, reflection_text, elements_used, tokens_used, memory_insights, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, user_id, text[:4000], elements, tokens_used, None, now),
            commit=True,
        )
        self._log_audit(tenant_id, user_id, "reflection_execute", "reflection_queue", reflection_id, {"tokens_used": tokens_used})
        return True

    def get_reflection_history(self, tenant_id: str, user_id: str, limit: int = 10) -> List[dict]:
        rows = self._exec(
            "SELECT id, reflection_text, elements_used, tokens_used, created_at FROM reflection_log WHERE tenant_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, user_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": int(r["id"]),
                "reflection_text": str(r["reflection_text"]),
                "elements_used": json.loads(r["elements_used"]) if r["elements_used"] else [],
                "tokens_used": int(r["tokens_used"] or 0),
                "created_at": int(r["created_at"]),
            })
        return out

    def register_agent(self, tenant_id: str, agent_name: str, agent_type: str = "subagent", permissions: str = "read") -> int:
        now = int(time.time())
        clean_agent_name = agent_name.strip()
        if not clean_agent_name:
            return 0
        a_type = "main" if agent_type == "main" else "subagent"
        allowed = {"read", "propose", "write"}
        perms = permissions if permissions in allowed else "read"
        if a_type == "subagent" and perms == "write":
            perms = "propose"
        row = self._exec(
            "SELECT id FROM agents WHERE tenant_id = ? AND agent_name = ?",
            (tenant_id, clean_agent_name),
        ).fetchone()
        if row:
            self._exec(
                "UPDATE agents SET agent_type = ?, permissions = ?, active = 1, last_active_at = ? WHERE id = ?",
                (a_type, perms, now, int(row["id"])),
                commit=True,
            )
            aid = int(row["id"])
        else:
            cur = self._exec(
                "INSERT INTO agents(tenant_id, agent_name, agent_type, permissions, active, created_at, last_active_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (tenant_id, clean_agent_name, a_type, perms, now, now),
                commit=True,
            )
            aid = int(cur.lastrowid)
        self._log_audit(tenant_id, f"agent:{clean_agent_name}", "agent_register", "agents", aid, {"agent_type": a_type, "permissions": perms})
        return aid

    def get_agent(self, tenant_id: str, agent_name: str) -> Optional[dict]:
        row = self._exec(
            "SELECT id, agent_name, agent_type, permissions, active, last_active_at FROM agents WHERE tenant_id = ? AND agent_name = ? AND active = 1",
            (tenant_id, agent_name),
        ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "agent_name": str(row["agent_name"]),
            "agent_type": str(row["agent_type"]),
            "permissions": str(row["permissions"]),
            "active": bool(row["active"]),
            "last_active_at": int(row["last_active_at"]) if row["last_active_at"] else None,
        }

    def can_propose(self, tenant_id: str, agent_name: str) -> bool:
        agent = self.get_agent(tenant_id, agent_name)
        return bool(agent and agent["permissions"] in {"propose", "write"})

    def submit_proposal(self, tenant_id: str, agent_name: str, target_store: str, proposal_type: str, content: str, confidence: str = "medium", priority: int = 1) -> Optional[int]:
        if not self.can_propose(tenant_id, agent_name):
            return None
        if target_store not in {"facts", "entities", "relations", "identity", "soul"}:
            return None
        allowed_by_store = {
            "facts": {"add", "delete"},
            "entities": {"add"},
            "relations": {"add"},
            "identity": {"add", "update"},
            "soul": {"add", "update"},
        }
        if proposal_type not in allowed_by_store.get(target_store, set()):
            return None
        if not content or len(content) > 8000:
            return None
        c_row = self._exec(
            "SELECT COUNT(*) AS c FROM memory_proposals WHERE tenant_id = ? AND agent_name = ? AND status = 'pending'",
            (tenant_id, agent_name),
        ).fetchone()
        if int(c_row["c"]) >= self.config.max_pending_proposals:
            return None
        now = int(time.time())
        prio = max(1, min(5, int(priority)))
        conf = confidence if confidence in {"low", "medium", "high"} else "medium"
        cur = self._exec(
            "INSERT INTO memory_proposals(tenant_id, agent_name, target_store, proposal_type, content, confidence, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (tenant_id, agent_name, target_store, proposal_type, content, conf, prio, now, now),
            commit=True,
        )
        pid = int(cur.lastrowid)
        self._log_audit(tenant_id, f"agent:{agent_name}", "proposal_submit", target_store, pid, {"proposal_type": proposal_type, "confidence": conf, "priority": prio})
        return pid

    def get_pending_proposals(self, tenant_id: str, target_store: Optional[str] = None, agent_name: Optional[str] = None, limit: int = 20) -> List[dict]:
        query = "SELECT id, agent_name, target_store, proposal_type, content, confidence, priority, created_at FROM memory_proposals WHERE tenant_id = ? AND status = 'pending'"
        params: list = [tenant_id]
        if target_store:
            query += " AND target_store = ?"
            params.append(target_store)
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        query += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        params.append(limit)
        rows = self._exec(query, tuple(params)).fetchall()
        return [{
            "id": int(r["id"]),
            "agent_name": str(r["agent_name"]),
            "target_store": str(r["target_store"]),
            "proposal_type": str(r["proposal_type"]),
            "content": str(r["content"]),
            "confidence": str(r["confidence"]),
            "priority": int(r["priority"]),
            "created_at": int(r["created_at"]),
        } for r in rows]

    def approve_proposal(self, proposal_id: int, tenant_id: str, reviewer_agent: str, review_comment: str = "") -> bool:
        reviewer = self.get_agent(tenant_id, reviewer_agent)
        if not reviewer or reviewer["permissions"] != "write":
            return False
        now = int(time.time())
        claimed = self._exec(
            "UPDATE memory_proposals SET status = 'approved', reviewed_by = ?, review_comment = ?, approved_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ? AND status = 'pending'",
            (reviewer_agent, review_comment[:240], now, now, proposal_id, tenant_id),
            commit=True,
        )
        if (claimed.rowcount or 0) <= 0:
            return False
        row = self._exec(
            "SELECT * FROM memory_proposals WHERE id = ? AND tenant_id = ? AND status = 'approved' AND reviewed_by = ?",
            (proposal_id, tenant_id, reviewer_agent),
        ).fetchone()
        if not row:
            return False
        ok = self._execute_proposal_content(
            tenant_id=tenant_id,
            agent_name=str(row["agent_name"]),
            target_store=str(row["target_store"]),
            proposal_type=str(row["proposal_type"]),
            content=str(row["content"]),
        )
        if not ok:
            self._exec(
                "UPDATE memory_proposals SET status = 'rejected', reviewed_by = NULL, review_comment = NULL, approved_at = NULL, rejection_reason = ?, rejected_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ? AND status = 'approved' AND reviewed_by = ?",
                ("execution_failed", now, now, proposal_id, tenant_id, reviewer_agent),
                commit=True,
            )
            self._log_audit(tenant_id, f"agent:{reviewer_agent}", "proposal_reject", str(row["target_store"]), proposal_id, {"original_agent": str(row["agent_name"]), "proposal_type": str(row["proposal_type"]), "reason": "execution_failed"})
            return False
        self._exec(
            "UPDATE memory_proposals SET status = 'executed', executed_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ? AND status = 'approved' AND reviewed_by = ?",
            (now, now, proposal_id, tenant_id, reviewer_agent),
            commit=True,
        )
        self._log_audit(tenant_id, f"agent:{reviewer_agent}", "proposal_approve", str(row["target_store"]), proposal_id, {"original_agent": str(row["agent_name"]), "proposal_type": str(row["proposal_type"])})
        return True

    def reject_proposal(self, proposal_id: int, tenant_id: str, reviewer_agent: str, rejection_reason: str = "") -> bool:
        reviewer = self.get_agent(tenant_id, reviewer_agent)
        if not reviewer or reviewer["permissions"] != "write":
            return False
        row = self._exec(
            "SELECT agent_name, target_store, proposal_type FROM memory_proposals WHERE id = ? AND tenant_id = ? AND status = 'pending'",
            (proposal_id, tenant_id),
        ).fetchone()
        if not row:
            return False
        now = int(time.time())
        cur = self._exec(
            "UPDATE memory_proposals SET status = 'rejected', reviewed_by = ?, rejection_reason = ?, rejected_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ? AND status = 'pending'",
            (reviewer_agent, rejection_reason[:240], now, now, proposal_id, tenant_id),
            commit=True,
        )
        if (cur.rowcount or 0) <= 0:
            return False
        self._log_audit(tenant_id, f"agent:{reviewer_agent}", "proposal_reject", str(row["target_store"]), proposal_id, {"original_agent": str(row["agent_name"]), "proposal_type": str(row["proposal_type"]), "reason": rejection_reason[:240]})
        return True

    def _execute_proposal_content(self, tenant_id: str, agent_name: str, target_store: str, proposal_type: str, content: str) -> bool:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return False
        try:
            if target_store == "facts":
                if proposal_type not in {"add", "delete"}:
                    return False
                if proposal_type == "add":
                    fact_text = str(data.get("fact", "")).strip()
                    if not fact_text:
                        return False
                    self.remember_fact(
                        session_id=str(data.get("session_id", "default")),
                        fact=fact_text,
                        tag=str(data.get("tag", "proposed")),
                        priority=int(data.get("priority", 1)),
                        memory_scope=str(data.get("memory_scope", "user")),
                        memory_type=str(data.get("memory_type", "semantic")),
                        user_id=str(data.get("user_id", "anonymous")),
                        tenant_id=tenant_id,
                    )
                elif proposal_type == "delete":
                    pattern = str(data.get("pattern", "")).strip()
                    if not pattern:
                        return False
                    self.forget_facts(
                        session_id=str(data.get("session_id", "default")),
                        pattern=pattern,
                        user_id=str(data.get("user_id", "anonymous")),
                        tenant_id=tenant_id,
                    )
            elif target_store == "entities" and proposal_type == "add":
                if not str(data.get("entity_name", "")).strip():
                    return False
                self.upsert_entity(
                    tenant_id=tenant_id,
                    user_id=str(data.get("user_id", "anonymous")),
                    entity_name=str(data.get("entity_name", "")).strip(),
                    entity_type=str(data.get("entity_type", "concept")),
                    description=str(data.get("description", "")),
                    confidence=float(data.get("confidence", 0.7)),
                )
            elif target_store == "relations" and proposal_type == "add":
                if int(data.get("source_entity_id", 0)) <= 0 or int(data.get("target_entity_id", 0)) <= 0:
                    return False
                self.upsert_relation(
                    tenant_id=tenant_id,
                    user_id=str(data.get("user_id", "anonymous")),
                    source_entity_id=int(data.get("source_entity_id", 0)),
                    target_entity_id=int(data.get("target_entity_id", 0)),
                    relation_type=str(data.get("relation_type", "related_to")),
                    strength=float(data.get("strength", 0.7)),
                )
            elif target_store == "identity" and proposal_type in {"add", "update"}:
                if not str(data.get("content", "")).strip():
                    return False
                self.set_identity(
                    tenant_id=tenant_id,
                    category=str(data.get("category", "general")),
                    content=str(data.get("content", "")).strip(),
                    stability=str(data.get("stability", "dynamic")),
                )
            elif target_store == "soul" and proposal_type in {"add", "update"}:
                if not str(data.get("content", "")).strip():
                    return False
                self.set_soul(
                    tenant_id=tenant_id,
                    category=str(data.get("category", "values")),
                    content=str(data.get("content", "")).strip(),
                )
            else:
                return False
            return True
        except Exception:
            return False

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
        key = self._hash_prompt(prompt)
        row = self._exec(
            "SELECT response, expires_at FROM exact_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt_hash = ?",
            (*self._scope(session_id, user_id, tenant_id), key),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time()):
            self._exec(
                "DELETE FROM exact_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt_hash = ?",
                (*self._scope(session_id, user_id, tenant_id), key),
                commit=True,
            )
            return None
        return str(row["response"])

    def set_cached(self, session_id: str, prompt: str, response: str, user_id: str = "anonymous", tenant_id: str = "default") -> None:
        now = int(time.time())
        self._exec(
            "INSERT OR REPLACE INTO exact_cache_entries(tenant_id, user_id, session_id, prompt_hash, response, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*self._scope(session_id, user_id, tenant_id), self._hash_prompt(prompt), response, now + self.config.cache_ttl_seconds, now),
            commit=True,
        )

    def set_semantic_cached(self, session_id: str, prompt: str, response: str, embedding: List[float], user_id: str = "anonymous", tenant_id: str = "default") -> None:
        now = int(time.time())
        self._exec(
            "INSERT OR REPLACE INTO semantic_cache_entries(tenant_id, user_id, session_id, prompt, response, embedding_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*self._scope(session_id, user_id, tenant_id), prompt.strip(), response, json.dumps(embedding), now + self.config.cache_ttl_seconds, now),
            commit=True,
        )

    def get_semantic_cached(self, session_id: str, prompt: str, embedder: Callable[[str], List[float]], threshold: Optional[float] = None, candidate_limit: Optional[int] = None, user_id: str = "anonymous", tenant_id: str = "default") -> Optional[str]:
        now = int(time.time())
        self._exec(
            "DELETE FROM semantic_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND expires_at < ?",
            (*self._scope(session_id, user_id, tenant_id), now),
            commit=True,
        )
        query_embedding = embedder(prompt)
        if not query_embedding:
            return None
        rows = self._exec(
            "SELECT prompt, response, embedding_json FROM semantic_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT ?",
            (*self._scope(session_id, user_id, tenant_id), candidate_limit or self.config.semantic_candidate_limit),
        ).fetchall()

        best_sim = -1.0
        best_response = None
        expected_dim = len(query_embedding)
        malformed_prompts: list[str] = []
        for row in rows:
            try:
                emb = json.loads(row["embedding_json"])
            except json.JSONDecodeError:
                logger.warning("invalid embedding_json encountered")
                malformed_prompts.append(str(row["prompt"]))
                continue
            if len(emb) != expected_dim:
                logger.warning("embedding dimension mismatch, dropping entry")
                malformed_prompts.append(str(row["prompt"]))
                continue
            sim = self._cosine_similarity(query_embedding, emb)
            if sim > best_sim:
                best_sim, best_response = sim, str(row["response"])

        for bad_prompt in malformed_prompts:
            self._exec(
                "DELETE FROM semantic_cache_entries WHERE tenant_id = ? AND user_id = ? AND session_id = ? AND prompt = ?",
                (*self._scope(session_id, user_id, tenant_id), bad_prompt),
                commit=True,
            )

        if best_response is not None and best_sim >= (threshold if threshold is not None else self.config.semantic_similarity_threshold):
            return best_response
        return None

    def apply_fact_maintenance(self, tenant_id: Optional[str] = None, user_id: Optional[str] = None, session_id: Optional[str] = None) -> int:
        now = int(time.time())
        stale_cutoff = now - self.config.stale_fact_days * 24 * 3600
        unhit_cutoff = now - self.config.unhit_fact_days * 24 * 3600

        where_parts = []
        params: list = []
        if tenant_id is not None:
            where_parts.append("tenant_id = ?")
            params.append(tenant_id)
        if user_id is not None:
            where_parts.append("user_id = ?")
            params.append(user_id)
        if session_id is not None:
            where_parts.append("session_id = ?")
            params.append(session_id)
        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Degrade stale priority first (before updated_at refreshes from relevance maintenance).
        self._exec(
            f"UPDATE facts SET priority = CASE WHEN priority > 1 THEN priority - 1 ELSE 1 END, updated_at = ? WHERE updated_at < ?" + (" AND " + " AND ".join(where_parts) if where_parts else ""),
            (now, stale_cutoff, *params),
            commit=True,
        )

        status_filter = " AND memory_status != 'archived'" if where_sql else " WHERE memory_status != 'archived'"
        select_sql = "SELECT id, relevance_score, COALESCE(relevance_base, 1.0) AS relevance_base, hit_count, memory_type, COALESCE(last_accessed_at, created_at) AS last_accessed FROM facts" + where_sql + status_filter
        rows = self._exec(select_sql, tuple(params)).fetchall()
        for r in rows:
            score = self._relevance(float(r["relevance_base"]), int(r["last_accessed"]), max(1, int(r["hit_count"])), str(r["memory_type"]))
            self._exec(
                "UPDATE facts SET relevance_score = ?, memory_status = ?, updated_at = ? WHERE id = ?",
                (score, self._status(score), now, int(r["id"])),
                commit=True,
            )

        deleted_unhit = self._exec(
            f"DELETE FROM facts WHERE hit_count = 0 AND created_at < ? AND memory_scope = 'session'" + (" AND " + " AND ".join(where_parts) if where_parts else ""),
            (unhit_cutoff, *params),
            commit=True,
        ).rowcount or 0

        deleted_archived = self._exec(
            f"DELETE FROM facts WHERE relevance_score < ? AND memory_scope = 'session'" + (" AND " + " AND ".join(where_parts) if where_parts else ""),
            (self.config.archive_threshold, *params),
            commit=True,
        ).rowcount or 0

        return int(deleted_unhit + deleted_archived)

    def purge_expired_cache(self, tenant_id: Optional[str] = None, user_id: Optional[str] = None, session_id: Optional[str] = None) -> int:
        now = int(time.time())
        where_parts = ["expires_at < ?"]
        params: list = [now]
        if tenant_id is not None:
            where_parts.append("tenant_id = ?")
            params.append(tenant_id)
        if user_id is not None:
            where_parts.append("user_id = ?")
            params.append(user_id)
        if session_id is not None:
            where_parts.append("session_id = ?")
            params.append(session_id)
        where_sql = " WHERE " + " AND ".join(where_parts)
        exact = self._exec("DELETE FROM exact_cache_entries" + where_sql, tuple(params), commit=True).rowcount or 0
        semantic = self._exec("DELETE FROM semantic_cache_entries" + where_sql, tuple(params), commit=True).rowcount or 0
        deleted_facts = self._exec("DELETE FROM facts WHERE expires_at IS NOT NULL AND " + " AND ".join(where_parts), tuple(params), commit=True).rowcount or 0
        return int(exact + semantic + deleted_facts)


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
