"""SQLite state: what we've seen, where each company's job board lives, what we've alerted on."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import FundingEvent, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid TEXT PRIMARY KEY,
    company TEXT, company_key TEXT, title TEXT, url TEXT, source TEXT,
    location TEXT, posted_at TEXT, first_seen TEXT, last_seen TEXT,
    score REAL, alerted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_key);

CREATE TABLE IF NOT EXISTS companies (
    key TEXT PRIMARY KEY,
    name TEXT,
    ats TEXT,            -- greenhouse/lever/ashby/workable, NULL if unknown/not found
    slug TEXT,
    probed_at TEXT,      -- last time we tried to discover the ATS
    origin TEXT          -- watchlist / connection / funding / edgar
);

CREATE TABLE IF NOT EXISTS funding (
    url TEXT PRIMARY KEY,
    company TEXT, company_key TEXT, headline TEXT, source TEXT,
    amount_usd REAL, round TEXT, published TEXT, first_seen TEXT, summary TEXT
);
CREATE INDEX IF NOT EXISTS funding_company ON funding(company_key);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS leads (
    company_key TEXT PRIMARY KEY,
    first_alerted TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(companies)")}
        if "note" not in cols:  # context shown in the digest, e.g. a YC one-liner
            self.conn.execute("ALTER TABLE companies ADD COLUMN note TEXT")

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # --- jobs -----------------------------------------------------------------
    def upsert_job(self, job: Job, score: float) -> bool:
        """Insert or refresh a job. Returns True if this is the first time we've seen it."""
        now = _now()
        cur = self.conn.execute("SELECT 1 FROM jobs WHERE uid=?", (job.uid,))
        is_new = cur.fetchone() is None
        if is_new:
            self.conn.execute(
                "INSERT INTO jobs (uid, company, company_key, title, url, source, location,"
                " posted_at, first_seen, last_seen, score) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job.uid, job.company, job.company_key, job.title, job.url, job.source,
                 job.location, _iso(job.posted_at), now, now, score),
            )
        else:
            self.conn.execute("UPDATE jobs SET last_seen=?, score=? WHERE uid=?", (now, score, job.uid))
        return is_new

    def was_alerted(self, uid: str) -> bool:
        row = self.conn.execute("SELECT alerted FROM jobs WHERE uid=?", (uid,)).fetchone()
        return bool(row and row["alerted"])

    def mark_alerted(self, uids: list[str]) -> None:
        self.conn.executemany("UPDATE jobs SET alerted=1 WHERE uid=?", [(u,) for u in uids])

    def has_any_jobs(self) -> bool:
        return self.conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchone() is not None

    def known_job_count(self, company_key: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE company_key=?", (company_key,)).fetchone()[0]

    # --- companies --------------------------------------------------------------
    def get_company(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM companies WHERE key=?", (key,)).fetchone()

    def upsert_company(self, key: str, name: str, origin: str,
                       ats: str | None = None, slug: str | None = None,
                       probed: bool = False, note: str | None = None) -> None:
        row = self.get_company(key)
        if row is None:
            self.conn.execute(
                "INSERT INTO companies (key, name, ats, slug, probed_at, origin, note) VALUES (?,?,?,?,?,?,?)",
                (key, name, ats, slug, _now() if probed else None, origin, note),
            )
            return
        self.conn.execute(
            "UPDATE companies SET ats=COALESCE(?, ats), slug=COALESCE(?, slug),"
            " probed_at=COALESCE(?, probed_at), note=COALESCE(?, note) WHERE key=?",
            (ats, slug, _now() if probed else None, note, key),
        )

    def notes(self) -> dict[str, str]:
        return {r["key"]: r["note"] for r in self.conn.execute(
            "SELECT key, note FROM companies WHERE note IS NOT NULL")}

    def needs_probe(self, key: str, reprobe_after_days: int) -> bool:
        row = self.get_company(key)
        if row is None or row["ats"]:
            return row is None
        if not row["probed_at"]:
            return True
        last = datetime.fromisoformat(row["probed_at"])
        return datetime.now(timezone.utc) - last > timedelta(days=reprobe_after_days)

    def boards(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM companies WHERE ats IS NOT NULL").fetchall()

    # --- funding ----------------------------------------------------------------
    def add_funding(self, ev: FundingEvent) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO funding (url, company, company_key, headline, source, amount_usd,"
            " round, published, first_seen, summary) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ev.url, ev.company, ev.key, ev.headline, ev.source, ev.amount_usd, ev.round,
             _iso(ev.published), _now(), ev.summary[:2000]),
        )
        return cur.rowcount > 0

    def recent_funding(self, days: int = 120) -> dict[str, FundingEvent]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        out: dict[str, FundingEvent] = {}
        for r in self.conn.execute(
            "SELECT * FROM funding WHERE first_seen >= ? ORDER BY first_seen", (cutoff,)
        ):
            out[r["company_key"]] = FundingEvent(
                company=r["company"], headline=r["headline"], url=r["url"], source=r["source"],
                published=datetime.fromisoformat(r["published"]) if r["published"] else None,
                amount_usd=r["amount_usd"], round=r["round"], summary=r["summary"] or "",
            )
        return out

    # --- outreach leads ---------------------------------------------------------
    def lead_is_new(self, company_key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM leads WHERE company_key=?", (company_key,)).fetchone() is None

    def mark_lead(self, company_key: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO leads VALUES (?, ?)", (company_key, _now()))

    # --- meta -------------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.conn.commit()
