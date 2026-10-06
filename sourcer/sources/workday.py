"""Workday career sites (most large companies: Zillow, Expedia, Redfin, Priceline, ...).

Every Workday site is backed by a public JSON endpoint:
  POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
Big companies have thousands of openings, so instead of listing everything we run a
few searches for your target titles and fetch details only for title matches.

Configure a company in watchlist.yaml with its careers URL, e.g.
  - name: Zillow
    workday: https://zillow.wd5.myworkdayjobs.com/Zillow_Group_External
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from ..models import Job

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://(?P<tenant>[\w-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[\w-]+)")
DEFAULT_SEARCHES = ["data scientist", "machine learning", "applied scientist", "analytics", "pricing",
                    "analyst", "business intelligence", "experimentation"]
PAGE = 20


def parse_url(url: str) -> tuple[str, str, str] | None:
    """careers URL -> (host, tenant, site)."""
    m = URL_RE.search(url)
    if not m:
        return None
    return f"{m['tenant']}.{m['wd']}.myworkdayjobs.com", m["tenant"], m["site"]


def _posted(text: str, start_date: str | None, now: datetime) -> datetime | None:
    if start_date:
        try:
            return datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    t = (text or "").lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    if m := re.search(r"(\d+)\+? days? ago", t):
        return now - timedelta(days=int(m.group(1)))
    return None


def _clean(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(text or "")).split())


def search(s: requests.Session, host: str, tenant: str, site: str, text: str,
           max_pages: int = 3) -> list[dict] | None:
    base = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out = []
    for page in range(max_pages):
        try:
            r = s.post(base, json={"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE,
                                   "searchText": text},
                       headers={"Accept": "application/json"}, timeout=(10, 30))
        except requests.RequestException as e:
            log.warning("workday %s: %s", host, e)
            return out or None
        if not r.ok:
            return out or None
        data = r.json()
        postings = data.get("jobPostings") or []
        out += postings
        if len(out) >= (data.get("total") or 0) or len(postings) < PAGE:
            break
    return out


def fetch_jobs(s: requests.Session, url: str, company: str, title_ok,
               searches: list[str] | None = None) -> list[Job] | None:
    """Jobs on a Workday site whose titles pass `title_ok(title)`. None if the site isn't reachable."""
    parsed = parse_url(url)
    if not parsed:
        return None
    host, tenant, site = parsed
    now = datetime.now(timezone.utc)
    seen: dict[str, dict] = {}
    reachable = False
    for q in searches or DEFAULT_SEARCHES:
        postings = search(s, host, tenant, site, q)
        if postings is None:
            continue
        reachable = True
        for p in postings:
            if p.get("externalPath") and title_ok(p.get("title", "")):
                seen.setdefault(p["externalPath"], p)
    if not reachable:
        return None
    jobs = []
    for path, p in seen.items():
        info = {}
        try:
            r = s.get(f"https://{host}/wday/cxs/{tenant}/{site}{path}",
                      headers={"Accept": "application/json"}, timeout=(10, 30))
            if r.ok:
                info = r.json().get("jobPostingInfo") or {}
        except requests.RequestException:
            pass
        jobs.append(Job(
            company=company, title=info.get("title") or p.get("title", ""),
            url=info.get("externalUrl") or f"https://{host}/{site}{path}",
            source="workday", external_id=f"{tenant}:{info.get('jobReqId') or path}",
            location=" / ".join(filter(None, [info.get("location") or p.get("locationsText", "")]
                                            + list(info.get("additionalLocations") or []))),
            description=_clean(info.get("jobDescription", "")),
            posted_at=_posted(p.get("postedOn", ""), info.get("startDate"), now),
            board=url,
        ))
    return jobs
