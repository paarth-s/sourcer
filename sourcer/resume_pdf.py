"""Compact, ATS-friendly resume PDFs from your Word/Google Doc base resumes.

Flow (used by the nightly routine, which can only attach files by passing their bytes as base64):
  1. `parse`  - the doc's plain text (Drive `read_file_content`) -> structured JSON.
  2. edit the JSON for the role (reorder/rephrase bullets, skills, objective).
  3. `render` - one-page Letter PDF in Helvetica (a PDF base font, so nothing is embedded and the file
     stays ~4-6 KB), with right-aligned dates and hanging-indent bullets. Refuses tailored bullets
     that introduce numbers the base resume doesn't contain.
  4. `verify` - after creating the Gmail draft, compare the attached PDF's bytes to the rendered file.
"""
from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import json
import re
from pathlib import Path

from .resume import numbers

SECTIONS = ("objective", "summary", "education", "skills", "experience")
_MON = r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?"
_DATES = re.compile(rf"\s+({_MON}(?:19|20)\d{{2}}\s*(?:[-–—]|to)\s*"
                    rf"(?:{_MON}(?:19|20)\d{{2}}|Present|Current|Now))\s*$")


def _clean(s: str) -> str:
    s = re.sub(r"\\([~*_\[\]#>-])", r"\1", s)            # markdown escapes from Drive's text rendering
    return re.sub(r"\s+", " ", s.replace("**", "")).strip(" \t•-*")


def _split_dates(line: str) -> tuple[str, str]:
    m = _DATES.search(line)
    return (line[:m.start()].strip(), m.group(1).strip()) if m else (line, "")


def parse_text(text: str) -> dict:
    """Plain text of a resume laid out like: name, contact, then Objective/Education/Skills/Experience."""
    lines = [_clean(l) for l in text.splitlines()]
    lines = [l for l in lines if l]
    out: dict = {"name": lines[0], "contact": lines[1] if len(lines) > 1 else "", "objective": "",
                 "education": [], "skills": [], "experience": []}
    section, i = None, 2
    while i < len(lines):
        line = lines[i]
        if line.lower() in SECTIONS:
            section = "objective" if line.lower() == "summary" else line.lower()
            i += 1
            continue
        if section == "objective":
            out["objective"] = f"{out['objective']} {line}".strip()
        elif section == "education":
            body, dates = _split_dates(line)
            out["education"].append({"text": body, "dates": dates})
        elif section == "skills":
            label, _, items = line.partition(" ")
            label = label.rstrip(":")
            out["skills"].append({"label": label, "items": items.lstrip(": ").strip()})
        elif section == "experience":
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            body, dates = _split_dates(nxt)
            if dates and not _split_dates(line)[1]:          # "Title" then "Company, City  dates"
                out["experience"].append({"title": line, "company": body, "dates": dates, "bullets": []})
                i += 2
                continue
            if out["experience"]:
                out["experience"][-1]["bullets"].append(line)
        i += 1
    return out


# --- fidelity ---------------------------------------------------------------------

def _all_text(r: dict) -> str:
    parts = [r.get("objective", "")] + [e["text"] for e in r.get("education", [])]
    for x in r.get("experience", []):
        parts += [x["title"], x["company"], *x["bullets"]]
    return " ".join(parts)


def check(tailored: dict, base: dict) -> list[str]:
    """Problems that would make the tailored resume untruthful or inconsistent with the base."""
    problems = []
    allowed = numbers(_all_text(base))
    for x in tailored.get("experience", []):
        for b in x["bullets"]:
            if bad := numbers(b) - allowed:
                problems.append(f"new number(s) {sorted(bad)} in: {b[:70]}")
    base_jobs = {(x["company"], x["dates"]) for x in base.get("experience", [])}
    for x in tailored.get("experience", []):
        if (x["company"], x["dates"]) not in base_jobs:
            problems.append(f"company/dates changed: {x['company']} {x['dates']}")
    if [e["dates"] for e in tailored.get("education", [])] != [e["dates"] for e in base.get("education", [])]:
        problems.append("education dates changed")
    base_skills = {s.strip().lower() for g in base.get("skills", []) for s in g["items"].split(",")}
    for g in tailored.get("skills", []):
        for s in g["items"].split(","):
            if s.strip() and s.strip().lower() not in base_skills:
                problems.append(f"skill not on base resume: {s.strip()}")
    for x in tailored.get("experience", [])[:2]:      # lead each main job with a result, not a responsibility
        if x["bullets"] and not re.search(r"\d", x["bullets"][0]) and any(re.search(r"\d", b) for b in x["bullets"]):
            problems.append(f"first bullet at {x['company']} has no number or metric - lead with a measurable result")
    for k in ("name", "contact"):
        if tailored.get(k) != base.get(k):
            problems.append(f"{k} changed")
    return problems


# --- rendering --------------------------------------------------------------------

def fmt_dates(d: str) -> str:
    """One date style everywhere: 'Jan 2026 - Sep 2026', '2024 - 2026' (base docs mix '–', '-' and spacing)."""
    return re.sub(r"\s*[-–—]\s*|\s+to\s+", " - ", d.strip())


def _all_dates(r: dict) -> list[str]:
    return [fmt_dates(e["dates"]) for e in r.get("education", []) + r.get("experience", []) if e.get("dates")]


def _story(r: dict, size: float):
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape as esc

    lead = size * 1.2
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=size, leading=lead)
    bold = ParagraphStyle("bb", parent=body, fontName="Helvetica-Bold")
    right = ParagraphStyle("r", parent=body, alignment=2)
    bullet = ParagraphStyle("li", parent=body, leftIndent=size * 1.5, bulletIndent=size * 0.4,
                            spaceAfter=size * 0.12)
    head = ParagraphStyle("h", parent=bold, fontSize=size + 1.2, leading=(size + 1.2) * 1.2,
                          spaceBefore=size * 0.55)
    W = 7.5 * 72 - 12                                   # page width minus margins and frame padding
    dw = max([stringWidth(d, "Helvetica", size) for d in _all_dates(r)] or [0]) + 6

    def row(left, dates):
        t = Table([[Paragraph(left, body), Paragraph(esc(fmt_dates(dates)), right)]], colWidths=[W - dw, dw])
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    def section(title):
        return [Paragraph(esc(title), head),
                HRFlowable(width="100%", thickness=0.6, color="black", spaceBefore=1, spaceAfter=size * 0.3)]

    s = [Paragraph(esc(r["name"]), ParagraphStyle("n", parent=bold, fontSize=size + 6, leading=(size + 6) * 1.2,
                                                   alignment=TA_CENTER)),
         Paragraph(esc(r["contact"]), ParagraphStyle("c", parent=body, fontSize=size - 0.5, alignment=TA_CENTER))]
    if r.get("objective"):
        s += section("Objective") + [Paragraph(esc(r["objective"]), body)]
    if r.get("education"):
        s += section("Education")
        for e in r["education"]:
            main, _, detail = e["text"].partition(" | ")
            s.append(row(f"<b>{esc(main)}</b>" + (f" | {esc(detail)}" if detail else ""), e["dates"]))
    if r.get("skills"):
        s += section("Skills")
        s += [Paragraph(f"<b>{esc(g['label'])}:</b> {esc(g['items'])}", body) for g in r["skills"]]
    if r.get("experience"):
        s += section("Experience")
        for n, x in enumerate(r["experience"]):
            if n:
                s.append(Spacer(1, size * 0.45))
            s.append(Paragraph(esc(x["title"]), bold))
            s.append(row(esc(x["company"]), x["dates"]))
            s += [Paragraph(esc(b), bullet, bulletText="\u2022") for b in x["bullets"]]
    return s


def render(r: dict, out: str | Path, sizes=(10.5, 10.25, 10, 9.75, 9.5, 9.25, 9)) -> dict:
    """Render to a single page, stepping the font size down until it fits."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate

    out = Path(out)
    for size in sizes:
        pages = []
        doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=36, rightMargin=36, topMargin=32,
                                bottomMargin=32, title=f"{r['name']} - Resume", author=r["name"],
                                pageCompression=1, invariant=1)
        doc.build(_story(r, size), onLaterPages=lambda c, d: pages.append(1))
        if not pages:
            break
    data = out.read_bytes()
    return {"path": str(out), "pages": 1 + len(pages), "font_size": size, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


# --- draft verification -------------------------------------------------------------

def attachment_from_raw(raw: str) -> tuple[str, bytes] | None:
    """First PDF attachment in a raw MIME message (as text, base64url, or a JSON tool result)."""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            raw = d.get("raw") or d.get("message", {}).get("raw") or raw
        except ValueError:
            pass
    if "Content-Type" not in raw[:5000]:
        try:
            raw = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
        except ValueError:
            return None
    msg = email.message_from_string(raw, policy=email.policy.default)
    for part in msg.walk():
        if part.get_content_type() == "application/pdf" or (part.get_filename() or "").lower().endswith(".pdf"):
            return part.get_filename() or "", part.get_payload(decode=True) or b""
    return None


def verify(raw: str, pdf: str | Path) -> tuple[bool, str]:
    want = Path(pdf).read_bytes()
    got = attachment_from_raw(raw)
    if not got:
        return False, "no PDF attachment found in the draft"
    name, data = got
    if data != want:
        return False, f"attachment {name!r} differs from {pdf} ({len(data)} vs {len(want)} bytes) - recreate the draft"
    return True, f"attachment {name!r} matches ({len(data)} bytes)"
