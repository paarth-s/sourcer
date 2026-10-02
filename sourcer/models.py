from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|technologies|"
    r"technology|labs|hq|ai|io|app|group|holdings)\b\.?",
    re.I,
)


def normalize_company(name: str) -> str:
    """Canonical key for matching company names across sources ("Acme AI, Inc." -> "acme")."""
    n = name.lower().replace("&", " and ")
    n = re.sub(r"\(.*?\)", " ", n)
    n = _SUFFIXES.sub(" ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(n.split())


@dataclass
class FundingEvent:
    company: str
    headline: str
    url: str
    source: str
    published: datetime | None = None
    amount_usd: float | None = None
    round: str | None = None
    summary: str = ""

    @property
    def key(self) -> str:
        return normalize_company(self.company)


@dataclass
class Job:
    company: str
    title: str
    url: str
    source: str               # greenhouse / lever / ashby / workable / hn
    external_id: str
    location: str = ""
    description: str = ""
    posted_at: datetime | None = None
    department: str = ""
    board: str = ""           # ATS board slug, when known

    def __post_init__(self):
        self.title = " ".join(self.title.split())
        self.company = " ".join(self.company.split())
        self.location = " ".join(self.location.split())

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def company_key(self) -> str:
        return normalize_company(self.company)


@dataclass
class Connection:
    first_name: str
    last_name: str
    company: str
    position: str
    url: str = ""
    connected_on: str = ""

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class ScoredJob:
    job: Job
    score: float
    reasons: list[str] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    funding: FundingEvent | None = None
    llm_note: str = ""
    packet: str = ""          # path to a prepared application packet, if any
    context: str = ""         # what the company does (YC one-liner etc.)


@dataclass
class OutreachLead:
    """A company worth contacting *before* a matching role is posted."""
    company: str
    score: float
    reasons: list[str]
    funding: FundingEvent | None = None
    connections: list[Connection] = field(default_factory=list)
    adjacent_roles: list[Job] = field(default_factory=list)
    search_links: dict[str, str] = field(default_factory=dict)
    draft_message: str = ""
