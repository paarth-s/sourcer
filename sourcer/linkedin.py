"""Your LinkedIn network, from LinkedIn's official data export.

LinkedIn has no API for your connections and scraping it violates their terms (and
gets accounts restricted), so sourcer reads the export instead:

  LinkedIn -> Settings & Privacy -> Data privacy -> Get a copy of your data
  -> "Connections" -> Request archive. You get Connections.csv by email in ~10 min.

Drop it at data/Connections.csv (or point SOURCER_CONNECTIONS at it). Refresh monthly.

The export holds 1st-degree connections only. For 2nd-degree ("mutual") paths, each
alert includes a pre-filtered LinkedIn people-search link you can click through.
"""
from __future__ import annotations

import csv
import io
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from .models import Connection, normalize_company


def parse_connections(text: str) -> list[Connection]:
    # The export starts with a few "Notes:" lines before the real header.
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("First Name")), None)
    if start is None:
        return []
    out = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        company = (row.get("Company") or "").strip()
        if not company:
            continue
        out.append(Connection(
            first_name=(row.get("First Name") or "").strip(),
            last_name=(row.get("Last Name") or "").strip(),
            company=company,
            position=(row.get("Position") or "").strip(),
            url=(row.get("URL") or "").strip(),
            connected_on=(row.get("Connected On") or "").strip(),
        ))
    return out


class Network:
    def __init__(self, connections: list[Connection], warm_contacts: dict | None = None):
        self.by_company: dict[str, list[Connection]] = defaultdict(list)
        for c in connections:
            self.by_company[normalize_company(c.company)].append(c)
        for company, people in (warm_contacts or {}).items():
            for p in people or []:
                self.by_company[normalize_company(company)].append(
                    Connection(first_name=str(p), last_name="", company=company, position="warm contact"))
        self.count = len(connections)

    @classmethod
    def load(cls, path: Path, warm_contacts: dict | None = None) -> "Network":
        path = Path(os.environ.get("SOURCER_CONNECTIONS", path))
        if not path.exists():
            return cls([], warm_contacts)
        return cls(parse_connections(path.read_text(encoding="utf-8-sig")), warm_contacts)

    def at(self, company: str) -> list[Connection]:
        """Your connections at `company`, most useful for a referral first."""
        return sorted(self.by_company.get(normalize_company(company), []), key=referral_rank)

    def companies(self) -> dict[str, str]:
        """normalized key -> display name, for every company where you know someone."""
        return {k: v[0].company for k, v in self.by_company.items() if k}


_RANKS = [
    re.compile(r"recruit|talent|sourcer|hiring", re.I),                       # can route you directly
    re.compile(r"(head|director|vp|manager|lead).*(data|machine learning|ml|ai|analytics|science)"
               r"|(data|ml|ai|analytics).*(head|director|vp|manager|lead)", re.I),  # likely hiring manager
    re.compile(r"data scien|machine learning|\bml\b|applied scien|analytics", re.I),  # future teammate
]


def referral_rank(c: Connection) -> int:
    for i, rx in enumerate(_RANKS):
        if rx.search(c.position or ""):
            return i
    return len(_RANKS)


def people_search_links(company: str) -> dict[str, str]:
    """LinkedIn search URLs for people at `company` - 2nd-degree is where mutual intros live."""
    base = "https://www.linkedin.com/search/results/people/?keywords="
    return {
        "2nd-degree, data/ML": base + quote(f'{company} data scientist OR machine learning')
        + "&network=%5B%22S%22%5D",
        "hiring managers": base + quote(f"{company} head of data OR head of ML OR VP engineering"),
        "founders": base + quote(f"{company} founder"),
    }
