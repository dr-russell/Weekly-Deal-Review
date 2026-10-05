import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import ARTIFACTS, DATA, DB

WRITE_LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS weeks (
            id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL,
            label TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'raw',
            report TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, received_at TEXT NOT NULL,
            subject TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blocks (
            id TEXT PRIMARY KEY, week_id TEXT NOT NULL REFERENCES weeks(id),
            message_id TEXT NOT NULL REFERENCES messages(id), source TEXT NOT NULL,
            received_at TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'pending',
            reason TEXT NOT NULL DEFAULT '', extraction TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY, week_id TEXT NOT NULL REFERENCES weeks(id),
            company TEXT NOT NULL, entity_key TEXT NOT NULL, fields TEXT NOT NULL,
            block_ids TEXT NOT NULL, sources TEXT NOT NULL, evidence TEXT NOT NULL,
            missing TEXT NOT NULL, portfolio_match TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS deals_week ON deals(week_id);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, week_id TEXT NOT NULL REFERENCES weeks(id),
            kind TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL,
            stage TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            elapsed REAL, timings TEXT NOT NULL DEFAULT '{}', metrics TEXT NOT NULL DEFAULT '{}',
            error TEXT, package TEXT, version TEXT NOT NULL DEFAULT 'v1'
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
            at TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
            run_id TEXT NOT NULL REFERENCES runs(id), deal_id TEXT NOT NULL REFERENCES deals(id),
            first_pass TEXT NOT NULL, verification TEXT, final TEXT NOT NULL,
            PRIMARY KEY(run_id,deal_id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES deals(id),
            fit TEXT NOT NULL, reason TEXT NOT NULL, previous TEXT,
            at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
        if "owner_pid" not in columns:
            db.execute("ALTER TABLE runs ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0")
        if "active" not in {r[1] for r in db.execute("PRAGMA table_info(deals)")}:
            db.execute("ALTER TABLE deals ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        for run in db.execute("SELECT id,owner_pid FROM runs WHERE status='running'").fetchall():
            try:
                if not run["owner_pid"]:
                    raise ProcessLookupError()
                os.kill(run["owner_pid"], 0)
            except ProcessLookupError:
                db.execute("UPDATE runs SET status='interrupted', error='Workflow process exited before completion', finished_at=? WHERE id=?", (now(), run["id"]))
            except PermissionError:
                # A sandbox may deny signal access to a live server process.
                pass
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(status) WHERE status='running'")


def query(sql, args=()):
    with connection() as db:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def execute(sql, args=()):
    with WRITE_LOCK, connection() as db:
        db.execute(sql, args)


def update_run(run_id, **values):
    allowed = {"status", "stage", "finished_at", "elapsed", "timings", "metrics", "error", "package"}
    if not values or not set(values) <= allowed:
        raise ValueError("Invalid run update")
    values = {k: encode(v) if k in {"timings", "metrics", "package"} else v for k, v in values.items()}
    execute(f"UPDATE runs SET {','.join(k+'=?' for k in values)} WHERE id=?", (*values.values(), run_id))


def event(run_id, stage, message, detail=None):
    execute("INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)", (run_id, now(), stage, message, encode(detail or {})))
    update_run(run_id, stage=stage)


def new_run(week_id, kind, target):
    run_id = uuid.uuid4().hex[:16]
    execute("INSERT INTO runs(id,week_id,kind,target,status,stage,started_at,owner_pid) VALUES(?,?,?,?,?,?,?,?)", (run_id, week_id, kind, target, "running", "queued", now(), os.getpid()))
    return run_id


def unpack(row, fields):
    for field in fields:
        if row.get(field) is not None:
            row[field] = json.loads(row[field])
    return row


def get_deals(week_id):
    return [unpack(r, ["fields", "block_ids", "sources", "evidence", "missing", "portfolio_match"]) for r in query("SELECT * FROM deals WHERE week_id=? AND active=1 ORDER BY company", (week_id,))]


def get_run(run_id):
    rows = query("SELECT * FROM runs WHERE id=?", (run_id,))
    if not rows:
        raise KeyError(run_id)
    row = unpack(rows[0], ["timings", "metrics", "package"])
    row["events"] = [unpack(r, ["detail"]) for r in query("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]
    row["decisions"] = [unpack(r, ["first_pass", "verification", "final"]) for r in query("SELECT d.*, x.company FROM decisions d JOIN deals x ON d.deal_id=x.id WHERE run_id=? ORDER BY x.company", (run_id,))]
    return row
