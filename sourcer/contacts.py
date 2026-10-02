"""Import an address-book export (Mac Contacts .vcf) as extra warm contacts.

Uses only what's in the file: the contact's company/title fields and, failing that,
their work email domain (jane@zillow.com -> Zillow). Personal-mail domains are ignored.
Nothing is looked up online.

Output is written in LinkedIn's Connections.csv format so the rest of sourcer treats
these people exactly like LinkedIn connections.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .models import Connection

PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com", "proton.me", "pm.me",
    "comcast.net", "att.net", "verizon.net", "sbcglobal.net", "qq.com", "163.com", "gmx.com",
    "fastmail.com", "hey.com", "duck.com", "yahoo.co.in", "rediffmail.com",
}
_SCHOOL = re.compile(r"\.(edu|ac\.[a-z]{2})$")


def _unfold(text: str) -> list[str]:
    """vCard lines can wrap: continuation lines start with a space or tab."""
    out: list[str] = []
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _value(line: str) -> str:
    v = line.split(":", 1)[1] if ":" in line else ""
    return v.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()


def company_from_domain(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower().strip(">")
    if not domain or domain in PERSONAL_DOMAINS or _SCHOOL.search(domain):
        return ""
    parts = domain.split(".")
    # mail.corp.acme.co.uk -> acme
    core = parts[-3] if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com") else parts[-2]
    return core.replace("-", " ").title() if len(parts) >= 2 else ""


def parse_vcards(text: str) -> list[Connection]:
    people: list[Connection] = []
    card: dict[str, list[str]] = {}
    for line in _unfold(text):
        key = line.split(":", 1)[0].split(";", 1)[0].upper()
        if key.startswith("ITEM") and "." in key:      # Apple groups fields as item1.EMAIL
            key = key.split(".", 1)[1]
        if key == "BEGIN":
            card = {}
        elif key == "END":
            people.extend(_card_to_connection(card))
        elif key in ("FN", "N", "ORG", "TITLE", "EMAIL"):
            card.setdefault(key, []).append(_value(line))
    return people


def _card_to_connection(card: dict[str, list[str]]) -> list[Connection]:
    name = (card.get("FN") or [""])[0]
    if not name and card.get("N"):
        last, first = (card["N"][0].split(";") + ["", ""])[:2]
        name = f"{first} {last}".strip()
    org = (card.get("ORG") or [""])[0].split(";")[0].strip()
    source = "address book"
    if not org:
        for email in card.get("EMAIL", []):
            if org := company_from_domain(email):
                source = "address book (from work email)"
                break
    if not name or not org:
        return []
    first, _, last = name.partition(" ")
    title = (card.get("TITLE") or [""])[0]
    return [Connection(first_name=first, last_name=last, company=org,
                       position=title or source)]


def write_connections_csv(people: list[Connection], path: Path) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"])
    for p in people:
        w.writerow([p.first_name, p.last_name, "", "", p.company, p.position, ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buf.getvalue())
