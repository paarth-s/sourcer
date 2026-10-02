"""Resume bank -> rendered resume (HTML/PDF), plus fidelity checks for tailored versions.

The bank (private/resume.yaml) holds every bullet from every resume variant. A tailored
resume is a *selection and rephrasing* of bank content; `verify` rejects any bullet that
introduces a number not in its source bullet, and skills are restricted to the bank.
"""
from __future__ import annotations

import glob
import html
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VARIANTS = ("ds", "mle", "product")


@dataclass
class Bullet:
    id: str
    text: str
    alt: dict[str, str] = field(default_factory=dict)

    def for_variant(self, v: str) -> str:
        return self.alt.get(v, self.text)

    def all_texts(self) -> list[str]:
        return [self.text, *self.alt.values()]


@dataclass
class Role:
    id: str
    company: str
    location: str
    dates: str
    titles: dict[str, str]
    bullets: list[Bullet]
    order: dict[str, list[str]] = field(default_factory=dict)

    def bullet(self, bid: str) -> Bullet | None:
        return next((b for b in self.bullets if b.id == bid), None)

    def bullets_for(self, v: str) -> list[Bullet]:
        ids = self.order.get(v) or [b.id for b in self.bullets]
        return [b for i in ids if (b := self.bullet(i))]


@dataclass
class Bank:
    contact: dict
    summaries: dict[str, str]
    education: list[dict]
    skills: dict[str, list[str]]
    skills_omit: dict[str, list[str]]
    roles: list[Role]

    @classmethod
    def load(cls, path: str | Path) -> "Bank":
        d = yaml.safe_load(Path(path).read_text())
        roles = [Role(id=r["id"], company=r["company"], location=r.get("location", ""),
                      dates=str(r.get("dates", "")), titles=r["titles"], order=r.get("order") or {},
                      bullets=[Bullet(id=b["id"], text=b["text"].strip(),
                                      alt={k: v.strip() for k, v in (b.get("alt") or {}).items() if v})
                               for b in r["bullets"]])
                 for r in d["experience"]]
        return cls(contact=d["contact"], summaries=d.get("summaries", {}),
                   education=d.get("education", []), skills=d.get("skills", {}),
                   skills_omit=d.get("skills_omit") or {}, roles=roles)

    def role(self, rid: str) -> Role | None:
        return next((r for r in self.roles if r.id == rid), None)

    def all_skills(self) -> list[str]:
        return [s for items in self.skills.values() for s in items]

    def as_text(self) -> str:
        """Plain-text dump of everything, for LLM context."""
        out = [f"Name: {self.contact.get('name')}"]
        for v, s in self.summaries.items():
            out.append(f"Summary ({v} variant): {s}")
        for e in self.education:
            out.append(f"Education: {e['degree']}, {e['school']} ({e['dates']}). {e.get('detail', '')}")
        out.append("Skills: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in self.skills.items()))
        for r in self.roles:
            titles = " / ".join(sorted(set(r.titles.values())))
            out.append(f"\n[{r.id}] {r.company}, {r.location}, {r.dates} - titles used: {titles}")
            for b in r.bullets:
                out.append(f"  ({b.id}) {b.text}")
                for v, t in b.alt.items():
                    out.append(f"  ({b.id}, {v} phrasing) {t}")
        return "\n".join(out)


@dataclass
class Resume:
    """A concrete, renderable resume."""
    variant: str
    summary: str
    skills: dict[str, list[str]]
    roles: list[tuple[Role, str, list[str]]]  # (role, title, bullet texts)


def base_resume(bank: Bank, variant: str) -> Resume:
    omit = set(bank.skills_omit.get(variant, []))
    return Resume(
        variant=variant, summary=bank.summaries.get(variant, ""),
        skills={k: [s for s in v if s not in omit] for k, v in bank.skills.items()},
        roles=[(r, r.titles.get(variant) or next(iter(r.titles.values())),
                [b.for_variant(variant) for b in r.bullets_for(variant)]) for r in bank.roles],
    )


_VARIANT_TERMS = {
    "mle": ["machine learning engineer", "ml engineer", "mlops", "production", "deploy", "inference",
            "latency", "infrastructure", "pipelines", "kubernetes", "serving", "software engineer"],
    "product": ["product analytics", "product data scientist", "product analyst", "metrics", "kpi",
                "dashboards", "stakeholder", "a/b", "experimentation", "funnel", "growth", "insights"],
    "ds": ["data scientist", "modeling", "statistics", "causal", "forecasting", "pricing",
           "recommendation", "machine learning"],
}


def pick_variant(title: str, description: str) -> str:
    """Closest base resume for a posting: title is decisive, description breaks ties."""
    t = title.lower()
    if re.search(r"\b(ml|machine learning|ai|mlops)\s+engineer\b|\bsoftware engineer\b", t):
        return "mle"
    if re.search(r"\bproduct\b|\banalytics\b|\banalyst\b|\bgrowth\b", t):
        return "product"
    text = f"{t} {description.lower()}"
    scores = {v: sum(text.count(term) for term in terms) for v, terms in _VARIANT_TERMS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > scores["ds"] else "ds"


# --- fidelity -------------------------------------------------------------------

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s?(?:%|[kmb]\b|x\b)?", re.I)


def numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group()).lower() for m in _NUM.finditer(text)}


def unsupported_numbers(new: str, sources: list[str]) -> set[str]:
    allowed = set().union(*(numbers(s) for s in sources)) if sources else set()
    return numbers(new) - allowed


# --- rendering ------------------------------------------------------------------

CSS = """
@page { size: Letter; margin: 0.45in 0.55in; }
body { font-family: 'Times New Roman', Times, serif; font-size: 10.4pt; line-height: 1.22; color: #000; margin: 0; }
h1 { text-align: center; font-size: 17pt; margin: 0 0 2px; font-weight: bold; }
.contact { text-align: center; font-size: 9.6pt; margin-bottom: 6px; }
h2 { font-size: 11.2pt; border-bottom: 1px solid #000; margin: 8px 0 3px; padding-bottom: 1px; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.role { margin-top: 4px; }
.role .title { font-weight: bold; }
.role .where { font-style: italic; }
ul { margin: 1px 0 0 0; padding-left: 18px; }
li { margin: 0 0 1px; }
.skills div { margin: 0 0 1px; }
.skills b { display: inline-block; min-width: 78px; }
p { margin: 0; }
"""


def render_html(bank: Bank, r: Resume) -> str:
    e = html.escape
    c = bank.contact
    contact = " &bull; ".join(e(str(c[k])) for k in ("phone", "email", "location", "linkedin") if c.get(k))
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(c.get('name', 'Resume'))}</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>{e(c.get('name', ''))}</h1><div class='contact'>{contact}</div>"]
    if r.summary:
        parts.append(f"<h2>Objective</h2><p>{e(r.summary)}</p>")
    parts.append("<h2>Education</h2>")
    for ed in bank.education:
        detail = f" | {e(ed['detail'])}" if ed.get("detail") else ""
        parts.append(f"<div class='row'><span><b>{e(ed['degree'])}</b>, {e(ed['school'])}{detail}</span>"
                     f"<span>{e(str(ed['dates']))}</span></div>")
    parts.append("<h2>Skills</h2><div class='skills'>")
    for cat, items in r.skills.items():
        if items:
            parts.append(f"<div><b>{e(cat)}</b> {e(', '.join(items))}</div>")
    parts.append("</div><h2>Experience</h2>")
    for role, title, bullets in r.roles:
        parts.append(f"<div class='role'><div class='row'><span class='title'>{e(title)}</span></div>"
                     f"<div class='row'><span class='where'>{e(role.company)}, {e(role.location)}</span>"
                     f"<span>{e(role.dates)}</span></div><ul>"
                     + "".join(f"<li>{e(b)}</li>" for b in bullets) + "</ul></div>")
    parts.append("</body></html>")
    return "".join(parts)


def find_chrome() -> str | None:
    env = os.environ.get("SOURCER_CHROME")
    if env:
        return env
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if p := shutil.which(name):
            return p
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(mac):
        return mac
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return found[-1] if found else None


def html_to_pdf(html_path: Path, pdf_path: Path) -> int | None:
    """Print HTML to PDF with headless Chrome. Returns page count, or None if Chrome is missing."""
    chrome = find_chrome()
    if not chrome:
        return None
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={profile}",
             "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
            check=True, capture_output=True, timeout=120,
        )
    data = pdf_path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", data)) or None
