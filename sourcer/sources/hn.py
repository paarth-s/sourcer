"""Hacker News monthly "Ask HN: Who is hiring?" thread (posted 1st weekday of the month)."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

from ..http import get_json, session
from ..models import Job

log = logging.getLogger(__name__)

ALGOLIA = "https://hn.algolia.com/api/v1"


def latest_thread_id(s) -> int | None:
    data = get_json(s, f"{ALGOLIA}/search_by_date",
                    params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
    for hit in (data or {}).get("hits", []):
        if hit.get("title", "").lower().startswith("ask hn: who is hiring"):
            return int(hit["objectID"])
    return None


def _text(raw: str) -> str:
    t = html.unescape(raw or "")
    t = re.sub(r"<p>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return t.strip()


def parse_thread(data: dict) -> list[Job]:
    jobs = []
    for c in (data or {}).get("children", []):
        text = _text(c.get("text") or "")
        if not text:
            continue
        first = text.split("\n", 1)[0].strip()
        parts = [p.strip() for p in first.split("|")]
        if len(parts) < 2:
            continue
        company = re.sub(r"\(.*?\)", "", parts[0]).strip()[:80]
        location = next((p for p in parts[1:] if re.search(
            r"remote|onsite|hybrid|, [A-Z]{2}\b|nyc|sf|london|berlin", p, re.I)), "")
        created = c.get("created_at")
        jobs.append(Job(
            company=company, title=" | ".join(parts[1:])[:200],
            url=f"https://news.ycombinator.com/item?id={c.get('id')}",
            source="hn", external_id=str(c.get("id")), location=location, description=text,
            posted_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        ))
    return jobs


def fetch_hn_jobs(cfg: dict) -> list[Job]:
    if not cfg.get("enabled", True):
        return []
    s = session()
    tid = latest_thread_id(s)
    if not tid:
        return []
    jobs = parse_thread(get_json(s, f"{ALGOLIA}/items/{tid}", timeout=60))
    log.info("hn: %d postings in thread %s", len(jobs), tid)
    return jobs
