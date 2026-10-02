"""Resolve a single posting URL to a Job, plus its application questions where the ATS exposes them.

Greenhouse publishes the full application form (custom questions included) on its public
API. Lever / Ashby / Workable don't, so for those pass the questions in a text file.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from ..http import get_json, session
from ..models import Job
from . import ats

EEO_RE = re.compile(r"gender|race|racial|ethnic|hispanic|latino|veteran|disabilit|sexual orientation|"
                    r"transgender|pronoun|demographic|identify as|equal (employment )?opportunity|"
                    r"\beeo|self[- ]identif|completion is voluntary|voluntary", re.I)


@dataclass
class Question:
    label: str
    required: bool = False
    type: str = "text"            # text / textarea / select / multiselect / file / boolean
    options: list[str] = field(default_factory=list)
    field_name: str = ""
    description: str = ""

    @property
    def is_eeo(self) -> bool:
        return bool(EEO_RE.search(self.label))


_GH = re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")
_LEVER = re.compile(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})")
_WORKABLE = re.compile(r"apply\.workable\.com/([\w-]+)/j/(\w+)")

_GH_TYPES = {"input_text": "text", "textarea": "textarea", "input_file": "file",
             "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}


def parse_greenhouse_questions(data: dict) -> list[Question]:
    out = []
    for q in (data.get("questions") or []) + (data.get("location_questions") or []):
        fields = q.get("fields") or [{}]
        f = fields[0]
        desc = re.sub(r"<[^>]+>", " ", html.unescape(q.get("description") or ""))
        out.append(Question(
            label=(q.get("label") or "").strip(), required=bool(q.get("required")),
            type=_GH_TYPES.get(f.get("type", ""), f.get("type", "text")),
            options=[v.get("label", "") for v in f.get("values") or []],
            field_name=f.get("name", ""), description=" ".join(desc.split()),
        ))
    # data["compliance"] / ["demographic_questions"] are EEO forms - deliberately not included.
    return out


def greenhouse_questions(slug: str, job_id: str) -> list[Question]:
    data = get_json(session(), f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?questions=true")
    return parse_greenhouse_questions(data) if data else []


def fetch_posting(url: str) -> tuple[Job, list[Question]] | None:
    s = session()
    if m := _GH.search(url):
        slug, jid = m.groups()
        data = get_json(s, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}?questions=true")
        if not data:
            return None
        board = get_json(s, f"https://boards-api.greenhouse.io/v1/boards/{slug}") or {}
        job = ats.parse_greenhouse({"jobs": [data]}, board.get("name") or slug)[0]
        return job, parse_greenhouse_questions(data)
    if m := _LEVER.search(url):
        slug, pid = m.groups()
        data = get_json(s, f"https://api.lever.co/v0/postings/{slug}/{pid}?mode=json")
        return (ats.parse_lever([data], slug)[0], []) if data else None
    if m := _ASHBY.search(url):
        slug, pid = m.groups()
        jobs = ats.fetch_board(s, "ashby", slug, slug) or []
        job = next((j for j in jobs if j.external_id == pid), None)
        return (job, []) if job else None
    if m := _WORKABLE.search(url):
        slug, code = m.groups()
        jobs = ats.fetch_board(s, "workable", slug, slug) or []
        job = next((j for j in jobs if j.external_id == code), None)
        return (job, []) if job else None
    return None


def questions_from_text(text: str) -> list[Question]:
    """One question per paragraph (blank-line separated) or per line if there are no blank lines."""
    chunks = re.split(r"\n\s*\n", text.strip()) if "\n\n" in text.strip() else text.strip().splitlines()
    return [Question(label=" ".join(c.split()), type="textarea") for c in chunks if c.strip()]
