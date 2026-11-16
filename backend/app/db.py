"""SQLite persistence (std-lib sqlite3, WAL). Swap for PostgreSQL by re-implementing this module."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL,
  created REAL, updated REAL, state TEXT NOT NULL, thumbnail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS versions(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created REAL, label TEXT, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachments(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT, mime TEXT, path TEXT,
  width INTEGER, height INTEGER, size INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role TEXT, content TEXT, meta TEXT, created REAL);
CREATE TABLE IF NOT EXISTS providers(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT, base_url TEXT, api_key TEXT,
  models TEXT, default_model TEXT, enabled INTEGER DEFAULT 1, created REAL);
CREATE TABLE IF NOT EXISTS mcp_servers(
  id TEXT PRIMARY KEY, name TEXT, url TEXT, transport TEXT, headers TEXT,
  enabled INTEGER DEFAULT 1, permission TEXT DEFAULT 'ask', disabled_tools TEXT DEFAULT '[]',
  tools_cache TEXT DEFAULT '[]', status TEXT DEFAULT 'unknown', created REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS team_messages(
  id TEXT PRIMARY KEY, project_id TEXT, job_id TEXT, round INTEGER, phase TEXT, agent_id TEXT, agent_name TEXT,
  role TEXT, kind TEXT, content TEXT, data TEXT, tokens INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS team_issues(
  id TEXT PRIMARY KEY, project_id TEXT, job_id TEXT, round INTEGER, author_id TEXT, author_name TEXT, severity TEXT,
  category TEXT, target TEXT, problem TEXT, evidence TEXT, qa_check TEXT, backed_by_test INTEGER, fix TEXT, status TEXT,
  reviews TEXT, decision TEXT, op_ids TEXT, version_before TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS team_qa(
  id TEXT PRIMARY KEY, project_id TEXT, job_id TEXT, round INTEGER, stage TEXT, report TEXT, created REAL);
CREATE TABLE IF NOT EXISTS agent_usage(
  key TEXT PRIMARY KEY, provider_id TEXT, model TEXT, calls INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, last_error TEXT DEFAULT '',
  last_ok REAL, last_latency_ms INTEGER, updated REAL);
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


_con = _connect()
_con.executescript(SCHEMA)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        try:
            yield _con
            _con.commit()
        except Exception:
            _con.rollback()
            raise


def q(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _con.execute(sql, args).fetchall()]


def q1(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


def get_setting(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with tx() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))
