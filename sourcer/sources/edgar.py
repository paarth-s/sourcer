"""SEC Form D filings via EDGAR full-text search.

Startups must file a Form D within 15 days of the first sale in a priced round, and
it's public immediately - often before the press release. A Form D for a company you
care about is a strong "they're about to hire" signal.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from ..http import get_json, sec_user_agent, session
from ..models import FundingEvent

log = logging.getLogger(__name__)

SEARCH = "https://efts.sec.gov/LATEST/search-index"
# Form D is dominated by pooled investment vehicles; skip those.
_VEHICLE = re.compile(
    r"\b(fund|funds|l\.?p\.?|capital|partners|holdings|trust|spv|series \w+|ventures|"
    r"investors|opportunit(y|ies)|feeder|master|portfolio|reit|aggregator|co-invest)\b", re.I)


def _display_name(src: dict) -> str:
    names = src.get("display_names") or []
    if not names:
        return ""
    return re.sub(r"\s*\(CIK \d+\)\s*$", "", names[0]).strip()


def parse_search(data: dict) -> list[FundingEvent]:
    out = []
    for hit in (data or {}).get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        name = _display_name(src)
        if not name or _VEHICLE.search(name):
            continue
        adsh, _, filename = hit.get("_id", "").partition(":")
        cik = (src.get("ciks") or ["0"])[0].lstrip("0") or "0"
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}"
               if adsh else "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=D")
        filed = src.get("file_date")
        loc = ", ".join(src.get("biz_locations") or [])
        out.append(FundingEvent(
            company=name.title() if name.isupper() else name,
            headline=f"Form D filed by {name}" + (f" ({loc})" if loc else ""),
            url=url, source="sec-form-d",
            published=datetime.fromisoformat(filed).replace(tzinfo=timezone.utc) if filed else None,
            round="Form D", summary=f"SEC Form D (exempt offering) filed {filed}. {loc}",
        ))
    return out


def search_form_d(term: str, lookback_days: int, user_agent: str, s=None) -> list[FundingEvent]:
    s = s or session(user_agent)
    end = date.today()
    params = {
        "q": f'"{term}"', "forms": "D", "dateRange": "custom",
        "startdt": (end - timedelta(days=lookback_days)).isoformat(), "enddt": end.isoformat(),
    }
    return parse_search(get_json(s, SEARCH, params=params))


def fetch_form_d(cfg: dict, company_names: list[str]) -> list[FundingEvent]:
    if not cfg.get("enabled", True):
        return []
    ua = sec_user_agent(cfg.get("user_agent", ""))
    s = session(ua)
    days = cfg.get("lookback_days", 14)
    events: dict[str, FundingEvent] = {}
    for term in cfg.get("keywords", []):
        for ev in search_form_d(term, days, ua, s):
            events.setdefault(ev.key, ev)
    # Exact-name lookups for companies you're already tracking.
    for name in company_names:
        for ev in search_form_d(name, days, ua, s):
            if ev.key == FundingEvent(company=name, headline="", url="", source="").key:
                events.setdefault(ev.key, ev)
    log.info("edgar: %d Form D filings", len(events))
    return list(events.values())
