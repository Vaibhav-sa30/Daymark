"""Durable local state for Daymark portfolio check-ins and update drafts.

The daemon and local HTTP server share this SQLite store. User events are
queued locally first so a temporary network or Supabase outage cannot lose them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse


DB_PATH = Path(__file__).with_name("portfolio_updates.sqlite3")
DEFAULT_SETTINGS = {
    "enabled": True,
    "interval_minutes": 1,
    "waking_start_hour": 9,
    "waking_end_hour": 21,
    "snooze_minutes": 60,
}
CATEGORIES = (
    "research_timeline",
    "research_papers",
    "research_implementations",
    "research_writeups",
    "research_artifacts",
    "ai_safety_writings",
    "writings_and_essays",
    "projects_and_implementations",
    "coursework_and_learning",
    "professional_experience",
    "other",
)
CHECKIN_RESPONSES = {"has_update", "nothing_to_update"}
UPDATE_STATUSES = {
    "draft", "processing", "awaiting_clarification", "awaiting_preview",
    "ready_for_review", "refinement_requested", "approved", "publishing",
    "published", "discarded", "failed",
}
PORTFOLIO_FILES = {
    "research_timeline": "research.html",
    "research_papers": "research.html",
    "research_implementations": "research-implementations.html",
    "research_writeups": "research-writeups.html",
    "research_artifacts": "artifacts.html",
    "ai_safety_writings": "writings.html",
    "writings_and_essays": "writings.html",
    "projects_and_implementations": "research-implementations.html",
    "coursework_and_learning": "coursework.html",
    "professional_experience": "about.html",
}
_SCHEMA_READY = set()


def _connect(db_path=None):
    path = str(db_path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _ensure_schema(conn, db_path=None):
    path = str(db_path or DB_PATH)
    if path in _SCHEMA_READY:
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portfolio_checkin_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            last_prompt_at TEXT,
            snooze_until TEXT
        );
        INSERT OR IGNORE INTO portfolio_checkin_state(singleton) VALUES (1);
        CREATE TABLE IF NOT EXISTS portfolio_checkins (
            id TEXT PRIMARY KEY,
            response TEXT NOT NULL CHECK (response IN ('has_update', 'nothing_to_update')),
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS portfolio_updates (
            id TEXT PRIMARY KEY,
            original_input TEXT NOT NULL,
            refinement_input TEXT,
            category TEXT NOT NULL,
            supporting_links_json TEXT NOT NULL DEFAULT '[]',
            proposal_json TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            branch_name TEXT,
            pull_request_number INTEGER,
            pull_request_url TEXT,
            commit_sha TEXT,
            preview_url TEXT,
            preview_status TEXT,
            production_url TEXT,
            production_status TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS portfolio_update_events (
            id TEXT PRIMARY KEY,
            update_id TEXT,
            event_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(update_id) REFERENCES portfolio_updates(id)
        );
        CREATE TABLE IF NOT EXISTS portfolio_event_outbox (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            synced_at TEXT,
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS portfolio_ui_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS portfolio_updates_created_idx
            ON portfolio_updates(created_at DESC);
        CREATE INDEX IF NOT EXISTS portfolio_events_created_idx
            ON portfolio_update_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS portfolio_outbox_pending_idx
            ON portfolio_event_outbox(synced_at, next_attempt_at);
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio_updates)")}
    for name, declaration in (
        ("refinement_input", "TEXT"), ("pull_request_number", "INTEGER"),
        ("pull_request_url", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE portfolio_updates ADD COLUMN {name} {declaration}")
    _SCHEMA_READY.add(path)


def _now(value=None):
    value = value or datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.astimezone()
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _insert_event(conn, event_type, metadata, update_id=None, created_at=None):
    event_id = str(uuid.uuid4())
    timestamp = (created_at or _now()).isoformat(timespec="seconds")
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "update_id": update_id,
        "metadata": metadata or {},
        "created_at": timestamp,
    }
    conn.execute(
        "INSERT INTO portfolio_update_events(id, update_id, event_type, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, update_id, event_type, _json(metadata or {}), timestamp),
    )
    conn.execute(
        "INSERT INTO portfolio_event_outbox(event_id, payload_json, next_attempt_at) VALUES (?, ?, ?)",
        (event_id, _json(payload), timestamp),
    )
    return event_id


def _settings(conn):
    values = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value_json FROM portfolio_settings"):
        try:
            values[row["key"]] = json.loads(row["value_json"])
        except json.JSONDecodeError:
            continue
    return values


def get_checkin_settings(db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        return _settings(conn)
    finally:
        conn.close()


def get_supabase_endpoint(db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        row = conn.execute("SELECT value_json FROM portfolio_settings WHERE key='supabase_ingest_url'").fetchone()
        return json.loads(row["value_json"]) if row else ""
    finally:
        conn.close()


def save_supabase_endpoint(value, db_path=None):
    value = str(value or "").strip()
    if value:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or "functions/v1/" not in parsed.path:
            raise ValueError("Use the HTTPS URL for the Supabase Edge Function")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute(
            "INSERT INTO portfolio_settings(key,value_json) VALUES ('supabase_ingest_url',?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (_json(value),),
        )
    finally:
        conn.close()


def save_checkin_settings(values, db_path=None):
    if not isinstance(values, dict):
        raise ValueError("Settings must be an object")
    current = get_checkin_settings(db_path)
    updated = {**current, **values}
    updated["enabled"] = bool(updated["enabled"])
    for key, low, high in (("interval_minutes", 1, 1440), ("waking_start_hour", 0, 23),
                           ("waking_end_hour", 0, 23), ("snooze_minutes", 5, 240)):
        try:
            updated[key] = int(updated[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number")
        if not low <= updated[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    if updated["waking_start_hour"] == updated["waking_end_hour"]:
        raise ValueError("Waking hours must cover at least one hour")

    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        for key, value in updated.items():
            conn.execute(
                "INSERT INTO portfolio_settings(key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key, _json(value)),
            )
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _in_waking_hours(hour, start, end):
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def claim_due_checkin(now=None, db_path=None):
    """Atomically claim one reminder, skipping missed intervals after sleep."""
    moment = _now(now)
    timestamp = moment.isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        config = _settings(conn)
        state = conn.execute(
            "SELECT last_prompt_at, snooze_until FROM portfolio_checkin_state WHERE singleton=1"
        ).fetchone()
        due = bool(config["enabled"] and _in_waking_hours(
            moment.hour, config["waking_start_hour"], config["waking_end_hour"]))
        if due and state["snooze_until"]:
            try:
                due = moment >= datetime.fromisoformat(state["snooze_until"])
            except ValueError:
                due = True
        elif due and not state["last_prompt_at"]:
            # Start the cadence at launch rather than interrupting immediately
            # on first use or after the feature is installed.
            conn.execute(
                "UPDATE portfolio_checkin_state SET last_prompt_at=? WHERE singleton=1",
                (timestamp,),
            )
            due = False
        elif due and state["last_prompt_at"]:
            try:
                previous = datetime.fromisoformat(state["last_prompt_at"])
                due = moment - previous >= timedelta(minutes=config["interval_minutes"])
            except ValueError:
                due = True
        if due:
            conn.execute(
                "UPDATE portfolio_checkin_state SET last_prompt_at=?, snooze_until=NULL WHERE singleton=1",
                (timestamp,),
            )
        conn.commit()
        return due
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def snooze_checkin(now=None, db_path=None):
    moment = _now(now)
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        config = _settings(conn)
        until = (moment + timedelta(minutes=config["snooze_minutes"])).isoformat(timespec="seconds")
        conn.execute("UPDATE portfolio_checkin_state SET snooze_until=? WHERE singleton=1", (until,))
        conn.commit()
        return until
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_checkin(response, source="manual", metadata=None, now=None, db_path=None):
    if response not in CHECKIN_RESPONSES:
        raise ValueError("Unsupported check-in response")
    moment = _now(now)
    checkin_id = str(uuid.uuid4())
    timestamp = moment.isoformat(timespec="seconds")
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO portfolio_checkins(id, response, source, created_at, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (checkin_id, response, str(source)[:40], timestamp, _json(safe_metadata)),
        )
        _insert_event(conn, "checkin_completed", {
            "checkin_id": checkin_id, "response": response, "source": str(source)[:40], **safe_metadata
        }, created_at=moment)
        conn.commit()
        return {"id": checkin_id, "response": response, "source": str(source)[:40], "created_at": timestamp}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _valid_link(value):
    parsed = urlparse(value)
    return parsed.scheme in {"https", "http"} and bool(parsed.netloc) and not any(c.isspace() for c in value)


def create_update(original_input, category="other", supporting_links=None, db_path=None):
    text = str(original_input or "").strip()
    if not text or len(text) > 10000:
        raise ValueError("Describe the update in 1–10,000 characters")
    if category not in CATEGORIES:
        raise ValueError("Choose a supported portfolio section")
    if supporting_links is None:
        supporting_links = []
    if not isinstance(supporting_links, list) or len(supporting_links) > 20:
        raise ValueError("Provide up to 20 supporting links")
    links = []
    for item in supporting_links:
        value = str(item or "").strip()
        if not value:
            continue
        if len(value) > 2048 or not _valid_link(value):
            raise ValueError("Each reference must be a valid HTTP or HTTPS link")
        links.append(value)

    update_id = str(uuid.uuid4())
    timestamp = _now().isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO portfolio_updates(id, original_input, category, supporting_links_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', ?, ?)",
            (update_id, text, category, _json(links), timestamp, timestamp),
        )
        _insert_event(conn, "update_submitted", {"category": category}, update_id, _now())
        conn.commit()
        return get_update(update_id, db_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_proposal(update_id, proposal, status="ready_for_review", error=None, db_path=None):
    if status not in UPDATE_STATUSES:
        raise ValueError("Unsupported portfolio update status")
    if not isinstance(proposal, dict):
        raise ValueError("Proposal must be an object")
    timestamp = _now().isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id,status FROM portfolio_updates WHERE id=?", (update_id,)).fetchone()
        if not row:
            raise ValueError("Portfolio update was not found")
        if row["status"] in {"discarded", "published"}:
            raise ValueError("This update is already closed")
        conn.execute(
            "UPDATE portfolio_updates SET proposal_json=?, status=?, error=?, updated_at=? WHERE id=?",
            (_json(proposal), status, str(error)[:1000] if error else None, timestamp, update_id),
        )
        event_type = "proposal_generated" if not error else "ai_proposal_failed"
        _insert_event(conn, event_type, {"status": status, "has_error": bool(error)}, update_id, _now())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_workflow_state(update_id, status, fields=None, event_type=None, metadata=None, db_path=None):
    if status not in UPDATE_STATUSES:
        raise ValueError("Unsupported portfolio update status")
    allowed_fields = {"refinement_input", "branch_name", "pull_request_number", "pull_request_url", "commit_sha",
                      "preview_url", "preview_status", "production_url", "production_status", "error"}
    fields = fields or {}
    if not isinstance(fields, dict) or set(fields) - allowed_fields:
        raise ValueError("Unsupported workflow fields")
    timestamp = _now().isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id,status FROM portfolio_updates WHERE id=?", (update_id,)).fetchone()
        if not row:
            raise ValueError("Portfolio update was not found")
        if row["status"] in {"discarded", "published"} and row["status"] != status:
            raise ValueError("This update is already closed")
        assignments = ["status=?", "updated_at=?"]
        values = [status, timestamp]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            values.append(str(value)[:2000] if value is not None else None)
        values.append(update_id)
        conn.execute(f"UPDATE portfolio_updates SET {', '.join(assignments)} WHERE id=?", values)
        if event_type:
            allowed_events = {"branch_created", "preview_deployment_ready", "update_refined",
                              "update_approved", "production_deployment_completed",
                              "update_discarded", "deployment_failed", "ai_proposal_failed"}
            if event_type not in allowed_events:
                raise ValueError("Unsupported portfolio lifecycle event")
            _insert_event(conn, event_type, metadata or {}, update_id, _now())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _update_dict(row):
    item = dict(row)
    item["supporting_links"] = json.loads(item.pop("supporting_links_json"))
    proposal = item.pop("proposal_json")
    item["proposal"] = json.loads(proposal) if proposal else None
    return item


def get_update(update_id, db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        row = conn.execute("SELECT * FROM portfolio_updates WHERE id=?", (update_id,)).fetchone()
        return _update_dict(row) if row else None
    finally:
        conn.close()


def list_updates(limit=100, db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        rows = conn.execute("SELECT * FROM portfolio_updates ORDER BY created_at DESC LIMIT ?",
                            (max(1, min(int(limit), 250)),)).fetchall()
        return [_update_dict(row) for row in rows]
    finally:
        conn.close()


def list_checkins(limit=100, db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        rows = conn.execute("SELECT * FROM portfolio_checkins ORDER BY created_at DESC LIMIT ?",
                            (max(1, min(int(limit), 250)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result
    finally:
        conn.close()


def list_update_events(update_id, db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        rows = conn.execute(
            "SELECT id,event_type,metadata_json,created_at FROM portfolio_update_events "
            "WHERE update_id=? ORDER BY rowid ASC", (update_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result
    finally:
        conn.close()


def queue_ui_command(command, db_path=None):
    if command not in {"new_portfolio_update"}:
        raise ValueError("Unsupported Daymark UI command")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("INSERT INTO portfolio_ui_commands(command, created_at) VALUES (?, ?)",
                     (command, _now().isoformat(timespec="seconds")))
    finally:
        conn.close()


def consume_ui_command(db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, command FROM portfolio_ui_commands ORDER BY id LIMIT 1").fetchone()
        if row:
            conn.execute("DELETE FROM portfolio_ui_commands WHERE id=?", (row["id"],))
        conn.commit()
        return row["command"] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def pending_outbox(limit=20, now=None, db_path=None):
    timestamp = _now(now).isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        rows = conn.execute(
            "SELECT event_id, payload_json, attempts FROM portfolio_event_outbox "
            "WHERE synced_at IS NULL AND next_attempt_at<=? ORDER BY next_attempt_at LIMIT ?",
            (timestamp, max(1, min(int(limit), 100))),
        ).fetchall()
        return [{"event_id": row["event_id"], "payload": json.loads(row["payload_json"]),
                 "attempts": row["attempts"]} for row in rows]
    finally:
        conn.close()


def mark_outbox_synced(event_id, now=None, db_path=None):
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        conn.execute("UPDATE portfolio_event_outbox SET synced_at=?, last_error=NULL WHERE event_id=?",
                     (_now(now).isoformat(timespec="seconds"), event_id))
    finally:
        conn.close()


def mark_outbox_failed(event_id, error, now=None, db_path=None):
    moment = _now(now)
    conn = _connect(db_path)
    try:
        _ensure_schema(conn, db_path)
        row = conn.execute("SELECT attempts FROM portfolio_event_outbox WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            return
        attempts = row["attempts"] + 1
        delay = min(24 * 60, 2 ** min(attempts, 10))
        retry_at = (moment + timedelta(minutes=delay)).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE portfolio_event_outbox SET attempts=?, next_attempt_at=?, last_error=? WHERE event_id=?",
            (attempts, retry_at, str(error)[:500], event_id),
        )
    finally:
        conn.close()


def sync_outbox(limit=5, db_path=None):
    """Retry genuine user events through a private Supabase Edge Function."""
    endpoint = os.environ.get("DAYMARK_SUPABASE_INGEST_URL", "").strip() or get_supabase_endpoint(db_path)
    ingest_token = os.environ.get("DAYMARK_SUPABASE_INGEST_TOKEN", "").strip()
    anon_key = os.environ.get("DAYMARK_SUPABASE_ANON_KEY", "").strip()
    if not ingest_token or not anon_key:
        try:
            import portfolio_credentials
            ingest_token = ingest_token or portfolio_credentials.get_secret("supabase_ingest_token")
            anon_key = anon_key or portfolio_credentials.get_secret("supabase_anon_key")
        except Exception:
            pass
    if not endpoint or not ingest_token or not anon_key:
        return 0
    import urllib.request
    sent = 0
    for record in pending_outbox(limit=limit, db_path=db_path):
        try:
            payload = _json(record["payload"]).encode("utf-8")
            request = urllib.request.Request(endpoint, data=payload, method="POST", headers={
                "Content-Type": "application/json",
                "apikey": anon_key,
                "Authorization": f"Bearer {anon_key}",
                "X-Daymark-Token": ingest_token,
                "Idempotency-Key": record["event_id"],
            })
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"Supabase returned HTTP {response.status}")
            mark_outbox_synced(record["event_id"], db_path=db_path)
            sent += 1
        except Exception as exc:
            mark_outbox_failed(record["event_id"], exc, db_path=db_path)
    return sent

