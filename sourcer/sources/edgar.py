"""SEC Form D filings from EDGAR.

Startups must file a Form D within 15 days of the first sale in a priced round, and
it's public immediately - often before the press release. That makes it the earliest
public "they just raised" signal.

EDGAR full-text search doesn't index recent Form D content, so we list every Form D in
the lookback window (filer name + location), drop investment vehicles by name, and:
  * always keep filers matching a tracked company or a profile keyword;
  * if SEC_USER_AGENT is set (SEC requires "Name email@domain" to read filings), read
    each remaining filing's XML for industry + offering size and keep tech companies
    raising $1M-$250M - stealth raises, before any press.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from ..http import get_json, sec_user_agent, session
from ..models import FundingEvent, normalize_company

log = logging.getLogger(__name__)

SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# Form D is dominated by pooled investment vehicles and property SPVs; skip those.
_VEHICLE = re.compile(
    r"\b(fund|funds|l\.?p\.?|llc|l\.l\.c\.|dst|capital|partners|holdings|trust|spv|series \w+|ventures|"
    r"investors|opportunit(y|ies)|feeder|master|portfolio|reit|aggregator|co-invest|offshore|"
    r"income|credit|lending|apartments|properties|realty fund|o&g|oil|gas|royalt\w*|minerals)\b", re.I)

# Form D industry groups worth scanning. (Real estate filings are overwhelmingly property
# SPVs; proptech startups usually file as "Other Technology".)
TECH_INDUSTRIES = {"Other Technology", "Computers", "Telecommunications", "Other Travel",
                   "Tourism and Travel Services", "Airlines and Airports", "Lodging and Conventions"}


def _display_name(src: dict) -> str:
    names = src.get("display_names") or []
    if not names:
        return ""
    return re.sub(r"\s*\((?:[A-Z.\-]+\)\s*\()?CIK \d+\)\s*$", "", names[0]).strip()


def _hit_to_event(hit: dict) -> FundingEvent | None:
    src = hit.get("_source", {})
    name = _display_name(src)
    if not name or _VEHICLE.search(name):
        return None
    adsh, _, filename = hit.get("_id", "").partition(":")
    cik = (src.get("ciks") or ["0"])[0].lstrip("0") or "0"
    url = f"{ARCHIVE}/{cik}/{adsh.replace('-', '')}/{filename}" if adsh else ""
    filed = src.get("file_date")
    loc = ", ".join(src.get("biz_locations") or [])
    return FundingEvent(
        company=name.title() if name.isupper() else name,
        headline=f"Form D filed by {name}" + (f" ({loc})" if loc else ""),
        url=url, source="sec-form-d",
        published=datetime.fromisoformat(filed).replace(tzinfo=timezone.utc) if filed else None,
        round="Form D", summary=f"SEC Form D (exempt offering) filed {filed}. {loc}",
    )


def parse_search(data: dict) -> list[FundingEvent]:
    return [ev for h in (data or {}).get("hits", {}).get("hits", []) if (ev := _hit_to_event(h))]


def list_form_d(s, lookback_days: int, max_pages: int = 30) -> list[FundingEvent]:
    """Every operating-company Form D filed in the window (EDGAR pages are 100 filings)."""
    end = date.today()
    params = {"forms": "D", "dateRange": "custom",
              "startdt": (end - timedelta(days=lookback_days)).isoformat(), "enddt": end.isoformat()}
    out: list[FundingEvent] = []
    for page in range(max_pages):
        data = get_json(s, SEARCH, params={**params, "from": page * 100})
        hits = (data or {}).get("hits", {}).get("hits", [])
        out += parse_search(data)
        if len(hits) < 100:
            break
        time.sleep(0.15)
    return out


def _tag(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", xml, re.S)
    return m.group(1) if m else ""


def enrich(ev: FundingEvent, xml: str) -> FundingEvent:
    """Add industry + offering size from the Form D XML."""
    industry = _tag(xml, "industryGroupType")
    offering = _tag(xml, "totalOfferingAmount")
    sold = _tag(xml, "totalAmountSold")
    amount = None
    for v in (offering, sold):
        if v.replace(".", "").isdigit() and float(v) > 0:
            amount = float(v)
            break
    ev.amount_usd = amount
    ev.summary = f"{ev.summary} Industry: {industry or 'n/a'}. Offering: {offering or 'n/a'}, sold: {sold or 'n/a'}."
    ev.headline = f"{ev.headline} - {industry}" if industry else ev.headline
    return ev


def is_startup_raise(ev: FundingEvent, xml: str) -> bool:
    if _tag(xml, "investmentFundType"):
        return False
    if _tag(xml, "industryGroupType") not in TECH_INDUSTRIES:
        return False
    return ev.amount_usd is None or 1e6 <= ev.amount_usd <= 250e6


def fetch_form_d(cfg: dict, tracked_names: list[str], keywords: list[str] | None = None) -> list[FundingEvent]:
    ua = sec_user_agent(cfg.get("user_agent", ""))
    # Opt-in: SEC only serves filing details to requests carrying a contact email, and
    # without them Form D is mostly noise. Set SEC_USER_AGENT="Name you@email" to enable.
    if not cfg.get("enabled", True) or "@" not in ua:
        return []
    can_read_filings = "@" in ua
    s = session(ua)
    filings = list_form_d(s, cfg.get("lookback_days", 3))
    tracked = {normalize_company(n) for n in tracked_names}
    kw = [k.lower() for k in (keywords or cfg.get("keywords", []))]

    keep: dict[str, FundingEvent] = {}
    rest: list[FundingEvent] = []
    for ev in filings:
        if ev.key in tracked or any(k in ev.company.lower() for k in kw):
            keep.setdefault(ev.key, ev)
        else:
            rest.append(ev)

    if can_read_filings:
        budget = cfg.get("max_filings_read", 250)
        for ev in rest[:budget]:
            if ev.key in keep or not ev.url:
                continue
            r = None
            try:
                r = s.get(ev.url, timeout=20)
            except Exception as e:  # noqa: BLE001 - network hiccup, skip this filing
                log.debug("form D %s: %s", ev.url, e)
            if r is not None and r.ok:
                enrich(ev, r.text)
                if is_startup_raise(ev, r.text):
                    keep.setdefault(ev.key, ev)
            time.sleep(0.12)  # SEC fair access: <10 requests/second
    else:
        log.info("edgar: SEC_USER_AGENT (with an email) not set - skipping filing details")
    log.info("edgar: %d Form D filings listed, %d kept", len(filings), len(keep))
    return list(keep.values())


# Kept for callers that search one name (selftest).
def search_form_d(term: str, lookback_days: int, user_agent: str, s=None) -> list[FundingEvent]:
    s = s or session(user_agent)
    return [ev for ev in list_form_d(s, lookback_days)
            if normalize_company(term) in ev.key or term.lower() in ev.company.lower()]
