"""Funding announcements from RSS (Google News searches + VC news feeds)."""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from ..http import get_text, session
from ..models import FundingEvent

log = logging.getLogger(__name__)

_VERBS = (r"raises|raised|lands|landed|secures|secured|closes|closed|nabs|bags|snags|"
          r"gets|grabs|picks up|scores|announces|completes|banks|attracts")
_AMOUNT = r"(?:US)?[$€£]\s?(?P<num>\d+(?:\.\d+)?)\s?(?P<unit>[MBK]|mn|bn|million|billion|thousand)?"
_ROUND = (r"(?P<round>pre-seed|seed|series [a-h]\+?|growth|bridge|strategic|venture|debt|"
          r"equity|funding|financing)")

# "Acme, the AI pricing startup, raises $12M Series A"
HEADLINE_RE = re.compile(
    rf"^(?P<company>[A-Z0-9][\w.&'’\- ]{{0,60}}?)(?:,[^,]{{0,90}},)?\s+(?:{_VERBS})\b"
    rf"(?P<rest>.*)$"
)
AMOUNT_RE = re.compile(_AMOUNT, re.I)
ROUND_RE = re.compile(_ROUND, re.I)
# Leading descriptors to strip: "AI pricing startup Acme raises..." -> "Acme"
_DESCRIPTOR_RE = re.compile(
    r"^(?:.*\b(?:startup|start-up|platform|company|firm|provider|maker|unicorn|fintech|proptech|"
    r"insurtech|traveltech))\s+(?P<name>[A-Z][\w.&'’\-]*(?: [A-Z][\w.&'’\-]*){0,3})$"
)
_FUNDING_HINT = re.compile(r"\b(raise|funding|seed|series [a-h]|round|investment|financing)\b", re.I)

_UNIT_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
              "b": 1e9, "bn": 1e9, "billion": 1e9}


def _strip_source(title: str) -> str:
    """Google News titles end in ' - Publisher'."""
    return re.sub(r"\s+[-–|]\s+[^-–|]{2,40}$", "", title).strip()


def parse_headline(title: str) -> tuple[str, float | None, str | None] | None:
    """Extract (company, amount_usd, round) from a funding headline, or None if it isn't one."""
    title = html.unescape(_strip_source(title))
    if not _FUNDING_HINT.search(title):
        return None
    m = HEADLINE_RE.match(title)
    if not m:
        return None
    company = m.group("company").strip(" -:,")
    d = _DESCRIPTOR_RE.match(company)
    if d:
        company = d.group("name")
    # Reject obvious non-company subjects.
    if company.lower() in {"startup", "the startup", "company", "it", "this startup"} or len(company) < 2:
        return None
    if len(company.split()) > 5:
        return None
    rest = m.group("rest")
    amount = None
    am = AMOUNT_RE.search(rest)
    if am:
        unit = (am.group("unit") or "").lower()
        amount = float(am.group("num")) * _UNIT_MULT.get(unit, 1.0)
    rd = ROUND_RE.search(rest)
    rnd = rd.group("round").title() if rd else None
    return company, amount, rnd


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str, source: str) -> list[FundingEvent]:
    """Parse RSS 2.0 or Atom; keep only items that look like funding announcements."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("bad feed %s: %s", source, e)
        return []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    out = []
    for it in items:
        title = (it.findtext("title") or it.findtext(f"{atom}title") or "").strip()
        link = it.findtext("link") or ""
        if not link:
            le = it.find(f"{atom}link")
            link = le.get("href", "") if le is not None else ""
        desc = it.findtext("description") or it.findtext(f"{atom}summary") or ""
        desc = re.sub(r"<[^>]+>", " ", html.unescape(desc))
        pub = _parse_date(it.findtext("pubDate") or it.findtext(f"{atom}updated"))
        parsed = parse_headline(title)
        if not parsed:
            continue
        company, amount, rnd = parsed
        out.append(FundingEvent(company=company, headline=_strip_source(title), url=link.strip(),
                                source=source, published=pub, amount_usd=amount, round=rnd,
                                summary=" ".join(desc.split())[:1500]))
    return out


def google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def fetch_funding(cfg: dict) -> list[FundingEvent]:
    s = session()
    feeds = [(google_news_url(q), f"google-news: {q}") for q in cfg.get("google_news_queries", [])]
    feeds += [(u, u) for u in cfg.get("rss_feeds", [])]
    events: dict[str, FundingEvent] = {}
    for url, label in feeds:
        text = get_text(s, url)
        if not text:
            continue
        for ev in parse_feed(text, label):
            # Same raise is covered by many outlets - keep the first per company.
            events.setdefault(ev.key, ev)
    log.info("funding: %d announcements from %d feeds", len(events), len(feeds))
    return list(events.values())
