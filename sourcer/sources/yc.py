"""Y Combinator companies that are currently hiring (public dataset, ~1,500 companies).

Filtered to companies whose description/tags hit your verticals or ML areas and that
hire in the US or remotely; their job boards are then discovered and scanned like any
other company. Catches startups that never show up in funding news.
"""
from __future__ import annotations

import logging
import re

from ..http import get_json, session

log = logging.getLogger(__name__)

URL = "https://yc-oss.github.io/api/companies/hiring.json"
US_REGIONS = {"United States of America", "America / Canada", "Remote", "Fully Remote", "Partly Remote"}


def matching_companies(data: list[dict], profile: dict) -> list[dict]:
    d = profile.get("domains", {})
    terms = [t.lower() for t in d.get("ml_areas", []) + d.get("verticals", [])]
    out = []
    for c in data or []:
        if c.get("status") != "Active" or not c.get("isHiring"):
            continue
        if not US_REGIONS & set(c.get("regions") or []):
            continue
        text = " ".join([c.get("one_liner") or "", c.get("long_description") or "",
                         " ".join(c.get("tags") or []), c.get("subindustry") or ""]).lower()
        hits = [t for t in terms if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", text)]
        if hits:
            out.append({"name": c["name"], "slug": c.get("slug", ""), "one_liner": c.get("one_liner", ""),
                        "batch": c.get("batch", ""), "team_size": c.get("team_size"),
                        "website": c.get("website", ""), "matched": hits[:4]})
    return out


def fetch_yc(profile: dict) -> list[dict]:
    data = get_json(session(), URL, timeout=(10, 60))
    companies = matching_companies(data, profile)
    log.info("yc: %d hiring companies match your profile", len(companies))
    return companies
