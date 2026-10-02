"""Public job-board APIs (no auth): Greenhouse, Lever, Ashby, Workable.

These are what companies' own careers pages read from, so a posting shows up here
the moment it's published - typically days before it's syndicated to LinkedIn/Indeed.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

import requests

from ..http import RateLimited, get_json
from ..models import Job, normalize_company

log = logging.getLogger(__name__)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(html.unescape(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):  # epoch millis (Lever)
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- per-provider fetch + parse -------------------------------------------------

def greenhouse_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def parse_greenhouse(data: dict, company: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            company=company, title=j.get("title", ""), url=j.get("absolute_url", ""),
            source="greenhouse", external_id=str(j.get("id")),
            location=(j.get("location") or {}).get("name", ""),
            description=_clean(j.get("content")),
            posted_at=_dt(j.get("first_published") or j.get("updated_at")),
            department=", ".join(d.get("name", "") for d in j.get("departments") or []),
        ))
    return jobs


def lever_url(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def parse_lever(data: list, company: str) -> list[Job]:
    jobs = []
    for j in data or []:
        cats = j.get("categories") or {}
        lists = " ".join(f"{l.get('text', '')} {_clean(l.get('content'))}" for l in j.get("lists") or [])
        jobs.append(Job(
            company=company, title=j.get("text", ""), url=j.get("hostedUrl", ""),
            source="lever", external_id=str(j.get("id")),
            location=cats.get("location", "") or "",
            description=" ".join([j.get("descriptionPlain", "") or "", lists,
                                  j.get("additionalPlain", "") or ""]).strip(),
            posted_at=_dt(j.get("createdAt")), department=cats.get("team", "") or "",
        ))
    return jobs


def ashby_url(slug: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def parse_ashby(data: dict, company: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        jobs.append(Job(
            company=company, title=j.get("title", ""), url=j.get("jobUrl", ""),
            source="ashby", external_id=str(j.get("id")), location=loc,
            description=j.get("descriptionPlain") or _clean(j.get("descriptionHtml")),
            posted_at=_dt(j.get("publishedAt")), department=j.get("department", "") or "",
        ))
    return jobs


def workable_url(slug: str) -> str:
    return f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"


def parse_workable(data: dict, company: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        if j.get("telecommuting"):
            loc = f"{loc} (Remote)".strip()
        jobs.append(Job(
            company=company, title=j.get("title", ""), url=j.get("url", ""),
            source="workable", external_id=str(j.get("shortcode") or j.get("id")), location=loc,
            description=_clean(j.get("description")),
            posted_at=_dt(j.get("published_on") or j.get("created_at")),
            department=j.get("department", "") or "",
        ))
    return jobs


def smartrecruiters_url(slug: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"


def parse_smartrecruiters(data: dict, company: str) -> list[Job]:
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        if loc.get("remote"):
            where = f"{where} (Remote)".strip()
        jobs.append(Job(
            company=company, title=j.get("name", ""),
            url=f"https://jobs.smartrecruiters.com/{(j.get('company') or {}).get('identifier', '')}/{j.get('id')}",
            source="smartrecruiters", external_id=str(j.get("id")), location=where,
            description=" ".join(filter(None, [(j.get("department") or {}).get("label"),
                                               (j.get("function") or {}).get("label")])),
            posted_at=_dt(j.get("releasedDate")),
        ))
    return jobs


PROVIDERS = {
    "greenhouse": (greenhouse_url, parse_greenhouse, "jobs"),
    "lever": (lever_url, parse_lever, None),
    "ashby": (ashby_url, parse_ashby, "jobs"),
    "workable": (workable_url, parse_workable, "jobs"),
    "smartrecruiters": (smartrecruiters_url, parse_smartrecruiters, "content"),
}


# Providers that rate-limited us during this process; skipped until the next run.
_THROTTLED: set[str] = set()


def fetch_board(s: requests.Session, ats: str, slug: str, company: str) -> list[Job] | None:
    """Return jobs on a board, or None if the board doesn't exist (or its ATS is throttling us)."""
    if ats in _THROTTLED:
        return None
    url_fn, parse_fn, key = PROVIDERS[ats]
    try:
        data = get_json(s, url_fn(slug), raise_rate_limit=True)
    except RateLimited:
        log.warning("%s is rate limiting; skipping it for the rest of this run", ats)
        _THROTTLED.add(ats)
        return None
    if data is None:
        return None
    if key and not (isinstance(data, dict) and key in data):
        return None
    if key is None and not isinstance(data, list):
        return None
    jobs = parse_fn(data, company)
    for j in jobs:
        j.board = slug
    return jobs


# --- discovery -------------------------------------------------------------------

def slug_candidates(name: str) -> list[str]:
    raw = name.lower().replace("&", "and")
    words = re.sub(r"[^a-z0-9 ]+", " ", raw).split()
    norm_words = normalize_company(name).split()
    cands = []
    for ws in (words, norm_words):
        if ws:
            cands += ["".join(ws), "-".join(ws)]
    if norm_words:
        cands += [norm_words[0] + "hq", norm_words[0] + "ai"]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def discover_board(s: requests.Session, name: str, providers: list[str]) -> tuple[str, str, list[Job]] | None:
    """Try slug guesses on each ATS. Returns (ats, slug, jobs) for the first board found."""
    for slug in slug_candidates(name):
        for ats in providers:
            jobs = fetch_board(s, ats, slug, name)
            if ats in ("smartrecruiters", "workable") and not jobs:
                continue  # these answer 200/empty for any unknown company name
            if jobs is not None:
                log.info("found %s board for %s at slug %r (%d jobs)", ats, name, slug, len(jobs))
                return ats, slug, jobs
    return None
