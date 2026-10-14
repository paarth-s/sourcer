"""Auto-apply: pick good-fit roles where you have no connection, prepare complete applications
for your review, then submit the ones you approve.

Nightly:   roles.json (from `run --preview`) -> select() -> queue.json
           forms.scan() fills in each form's questions; auto_answer() answers the standard ones
           (identity, work authorization, salary, EEO = decline...) from your facts; the rest are
           drafted by the agent and marked for review. render_doc() turns the queue into one
           Google Doc per night - that Doc is what you read, can edit, and approve.
Morning:   parse_doc() reads the (possibly edited) Doc back; forms.submit() files each approved one.

Nothing is submitted without an explicit approval, and CAPTCHAs are never worked around: a form
that shows one is handed back to you with its answers and resume ready.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .scoring import required_years
from .sources.events import BAY_AREA

# --- selection --------------------------------------------------------------------

FAMILY = re.compile(r"data scien|machine learning|\bml\b|\bmle\b|\bai\b|artificial intelligence|applied scien|"
                    r"deep learning|\bllm|\bnlp\b|computer vision|recommend|personali[sz]ation|ranking|"
                    r"decision scien|research engineer|inference|modeling", re.I)
ROLE = re.compile(r"scien|engineer|\bmle\b|developer", re.I)
NOT_FOR_YOU = re.compile(r"\b(staff|principal|lead|manager|management|director|head|vp|vice president|intern|"
                         r"internship|new grad|graduate program|(university|college|campus) grad\w*|early[- ]career|entry[- ]level|residency|apprentice|distinguished|architect|sales|solutions?|"
                         r"support|recruit\w*|product manager|analyst|contract|part[- ]time|postdoc|fellow)\b",
                         re.I)
SENIOR = re.compile(r"\b(senior|sr\.?)\b", re.I)
APPLIED = re.compile(r"\bapplied\b|production|deploy|ship|end[- ]to[- ]end|real[- ]world|product", re.I)
RESEARCHY = re.compile(r"research scientist|phd (is )?required|ph\.?d\.? (in|required)|publication record|"
                       r"first[- ]author", re.I)
BAY = re.compile(BAY_AREA.pattern + r"|bay area|silicon valley", re.I)
ATS_FORMS = {"greenhouse", "lever", "ashby"}


@dataclass
class Candidate:
    id: str                    # job uid, e.g. greenhouse:12345
    company: str
    title: str
    url: str
    apply_url: str
    ats: str
    location: str
    score: float
    rank: float
    why: list[str]
    variant: str               # ds | mle base resume
    required_years: int | None = None
    description: str = ""
    status: str = "pending"    # pending -> ready | needs_you -> submitted | blocked | failed | skipped
    answers: list[dict] = field(default_factory=list)   # {label, answer, how, required}
    note: str = ""


def apply_url(source: str, url: str, board: str, external_id: str) -> str:
    if source == "greenhouse" and board and external_id:
        # The embed form works even for companies whose board redirects to their own careers site.
        return f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={external_id}"
    if source == "lever":
        return url.rstrip("/") + ("" if url.rstrip("/").endswith("/apply") else "/apply")
    if source == "ashby" and board and external_id:
        return f"https://jobs.ashbyhq.com/{board}/{external_id}/application"
    return url


def pick_variant(title: str) -> str:
    t = title.lower()
    if re.search(r"engineer|\bmle\b|applied scien|research engineer|developer", t) and "data scien" not in t:
        return "mle"
    return "ds"


def is_fit(role: dict) -> tuple[bool, str]:
    title, loc = role.get("title", ""), role.get("location", "")
    if role.get("connections"):
        return False, "you have a connection there - warm outreach instead"
    if not (FAMILY.search(title) and ROLE.search(title)):
        return False, "not a DS / MLE / AI engineering title"
    if NOT_FOR_YOU.search(title):
        return False, "level or track doesn't fit"
    if not BAY.search(loc):
        return False, f"not Bay Area ({loc or 'no location'})"
    req = role.get("required_years")
    if req is not None and req > 4:
        return False, f"asks {req}+ years"
    if SENIOR.search(title) and (req is None or req > 4):
        return False, "senior title without a 2-4 year ask"
    if role.get("source") not in ATS_FORMS:
        return False, f"{role.get('source')} applications need an account - apply yourself"
    return True, ""


def select(roles: list[dict], exclude: set[str] = frozenset(), limit: int = 15,
           per_company: int = 2) -> tuple[list[Candidate], list[tuple[dict, str]]]:
    picked, skipped, per = [], [], {}
    ranked = []
    for r in roles:
        if r.get("uid") in exclude:
            continue
        ok, why = is_fit(r)
        if not ok:
            skipped.append((r, why))
            continue
        text = f"{r.get('title', '')} {r.get('description', '')}"
        rank = r.get("score", 0) + (8 if APPLIED.search(text) else 0) - (10 if RESEARCHY.search(text) else 0)
        ranked.append((rank, r))
    for rank, r in sorted(ranked, key=lambda x: -x[0]):
        key = re.sub(r"\W+", "", r["company"].lower())
        if per.get(key, 0) >= per_company or len(picked) >= limit:
            continue
        per[key] = per.get(key, 0) + 1
        picked.append(Candidate(
            id=r["uid"], company=r["company"], title=r["title"], url=r["url"],
            apply_url=apply_url(r["source"], r["url"], r.get("board", ""), r.get("external_id", "")),
            ats=r["source"], location=r.get("location", ""), score=r.get("score", 0), rank=rank,
            why=list(r.get("reasons") or [])[:4], variant=pick_variant(r["title"]),
            required_years=r.get("required_years"), description=(r.get("description") or "")[:8000]))
    return picked, skipped


def roles_payload(new_jobs) -> list[dict]:
    """ScoredJob list -> the JSON `run --preview` writes for the apply agent."""
    out = []
    for sj in new_jobs:
        j = sj.job
        out.append({"uid": j.uid, "company": j.company, "title": j.title, "url": j.url,
                    "location": j.location, "source": j.source, "board": j.board,
                    "external_id": j.external_id, "score": round(sj.score, 1), "reasons": sj.reasons,
                    "connections": [c.name for c in sj.connections],
                    "required_years": required_years(j.description),
                    "description": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(j.description or "")))[:8000]})
    return out


# --- standard answers -------------------------------------------------------------

DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (self[- ])?identify|choose not|"
                     r"not to disclose|rather not|i don.?t want", re.I)
EEO = re.compile(r"gender|race|racial|ethnic|hispanic|latin|veteran|disabilit|sexual orientation|lgbt|"
                 r"transgender|pronoun|demographic|self[- ]identif", re.I)


def _yes(opts):
    return next((o for o in opts if re.match(r"\s*yes\b", o, re.I)), "Yes" if not opts else None)


def _no(opts):
    return next((o for o in opts if re.match(r"\s*no\b", o, re.I)), "No" if not opts else None)


def auto_answer(label: str, kind: str, options: list[str], facts: dict, company: str = "") -> tuple[str | None, str]:
    """(answer, how) for questions with a known, factual answer; (None, "") otherwise.
    how: 'profile' (from your facts) or 'eeo-decline'."""
    l = re.sub(r"[\s*✱:]+$", "", " ".join(label.lower().split()))
    opts = [o for o in options if o.strip()]
    contact = facts.get("contact") or {}
    name = (contact.get("name") or "").split()

    def pick(*pats):
        for p in pats:
            for o in opts:
                if re.search(p, o, re.I):
                    return o
        return None

    if kind == "file":
        if re.search(r"resume|cv\b", l):
            return "<resume>", "profile"
        return None, ""
    if EEO.search(l):
        if opts:
            d = next((o for o in opts if DECLINE.search(o)), None)
            return (d, "eeo-decline") if d else (None, "")
        return ("", "eeo-decline") if "pronoun" in l else (None, "")
    if re.fullmatch(r"(legal )?first name\*?|preferred first name\*?", l) and name:
        return name[0], "profile"
    if re.fullmatch(r"last name\*?|surname\*?", l) and name:
        return " ".join(name[1:]), "profile"
    if re.fullmatch(r"(full )?name\*?|full legal name\*?", l) and name:
        return " ".join(name), "profile"
    if re.search(r"\be-?mail\b", l) and "confirm" not in l and not opts:
        return contact.get("email", ""), "profile"
    if re.search(r"\bphone\b", l) and not opts:
        return contact.get("phone", ""), "profile"
    if re.search(r"linkedin", l) and not opts:
        return contact.get("linkedin", ""), "profile"
    if re.search(r"^(current )?location|location \(city\)|city\b|where are you (currently )?(located|based)", l) and not opts:
        return facts.get("current_location", ""), "profile"
    if re.fullmatch(r"country\*?", l) and opts:
        o = pick(r"^united states")
        return (o, "profile") if o else (None, "")
    if re.search(r"authori[sz]ed to work|legally (eligible|authorized|able)|eligib\w+ to work|right to work", l):
        a = _yes(opts) if facts.get("work_authorized") else _no(opts)
        return a, "profile"
    if re.search(r"sponsor|visa|immigration", l):
        a = _yes(opts) if facts.get("requires_sponsorship") else _no(opts)
        return a, "profile"
    if re.search(r"(currently )?(located|reside|living|based) in the (us|u\.s|united states)", l):
        return _yes(opts), "profile"
    if re.search(r"relocat|commut|in[- ]office|hybrid|on-?site|work (from|in|at) (our|the)|based in|live in|"
                 r"located in|days (a|per) week", l) and BAY.search(l):
        return _yes(opts), "profile"
    if re.search(r"18 years|age of 18|over 18|at least 18", l):
        return _yes(opts), "profile"
    if re.search(r"salary|compensation expectation|pay expectation|desired (pay|comp)", l):
        if opts:
            return None, ""
        return facts.get("salary_expectation", ""), "profile"
    if re.search(r"how did you (hear|find|learn)|where did you (hear|find)|referral source|source\b", l):
        if opts:
            o = pick(r"career|company (web)?site|website", r"job board|linkedin", r"other")
            return (o, "profile") if o else (None, "")
        return facts.get("how_did_you_hear", "Company careers page"), "profile"
    if re.search(r"(previously|ever|formerly|currently) (been )?(employed|worked) (by|at|for)|"
                 r"(current|former) employee|worked (here|for us) before", l) and company:
        return _no(opts), "profile"
    if re.search(r"(consent|agree|acknowledge).*(privacy|data|processing|personal information|retain|"
                 r"terms|policy|notice)|(privacy|data processing).*(consent|agree|acknowledge)", l):
        o = pick(r"^\s*(yes|i (consent|agree|acknowledge|understand))") or (None if opts else "Yes")
        return (o, "profile") if o else (None, "")
    if re.search(r"(earliest|available|availability).*(start|begin)|start date|notice period", l) and not opts:
        return facts.get("earliest_start", ""), "profile"
    if re.search(r"website|portfolio|github|personal (site|url)|twitter|blog", l) and not opts:
        return facts.get(("github" if "github" in l else "website"), ""), "profile"
    return None, ""


# --- review Doc ---------------------------------------------------------------------

PROFILE_MARK = "[from your profile]"
IDENTITY = re.compile(r"name|e-?mail|phone|linkedin|resume|cv\b", re.I)
STATUS_TEXT = {"ready": "READY - will be submitted when you say go",
               "needs_you": "NEEDS YOU - answer the questions marked ??? in this Doc first",
               "manual": "APPLY YOURSELF - this form can't be submitted automatically"}


def render_doc(cands: list[Candidate], resumes: dict[str, str], date: str) -> str:
    """HTML for the nightly review Google Doc. `resumes` maps candidate id -> tailored resume text
    (Objective / Education / Skills / Experience sections, no name/contact lines)."""
    e = html.escape
    out = [f"<html><body><h1>Sourcer applications - {e(date)}</h1>",
           "<p>Reply <b>go</b> in the Sourcer session to submit every READY application below, or "
           "<b>go 1 3 5</b> for some of them. Edit any answer in this Doc first if you like - the "
           "submission uses this Doc's text. Delete an application's section to skip it.</p>"]
    for n, c in enumerate(cands, 1):
        out.append(f"<h2>{n}. {e(c.company)} - {e(c.title)}</h2>")
        out.append(f"<p>ID: {e(c.id)}</p><p>Apply: {e(c.apply_url)}</p><p>Location: {e(c.location)}</p>")
        out.append(f"<p>Status: {e(STATUS_TEXT.get(c.status, c.status))}</p>")
        if c.why or c.note:
            out.append(f"<p>Why: {e('; '.join(c.why))}{(' - ' + e(c.note)) if c.note else ''}</p>")
        out.append("<h3>Answers</h3>")
        for a in c.answers:
            req = " (required)" if a.get("required") else ""
            ans = a.get("answer")
            shown = "???" if ans in (None, "") and a.get("required") else (ans or "(blank)")
            if a.get("how") == "profile" and IDENTITY.search(a["label"]):
                shown = PROFILE_MARK
            if a.get("how") == "eeo-decline":
                shown += " [voluntary EEO question - declined]"
            out.append(f"<p>Q: {e(a['label'])}{req}</p>")
            if a.get("options") and len(a["options"]) <= 10 and a.get("how") != "eeo-decline":
                out.append(f"<p>Options: {e(' | '.join(a['options'][:12]))}</p>")
            out.append("<p>A: " + "<br>".join(e(x) for x in str(shown).split("\n")) + "</p>")
        if c.id in resumes:
            out.append(f"<h3>Resume (base: {e(c.variant)})</h3>")
            out += [f"<p>{e(line)}</p>" for line in resumes[c.id].splitlines() if line.strip()]
    out.append("</body></html>")
    return "\n".join(out)


def _unmd(s: str) -> str:
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|<])", r"\1", s)
    return s.replace("**", "").strip()


_HEAD = re.compile(r"^#{1,3}\s*(\d+)\.\s+(.*)$")


def parse_doc(text: str) -> list[dict]:
    """Read a review Doc (Drive's text rendering of it) back into [{n, id, apply_url, status, answers,
    resume_text, variant}]. Sections the user deleted are simply absent."""
    apps, cur, section = [], None, None
    for raw in text.splitlines():
        raw = raw.replace("**", "")
        line = _unmd(raw)
        if not line:
            continue
        m = _HEAD.match(raw.strip())
        if m:
            cur = {"n": int(m.group(1)), "heading": _unmd(m.group(2)), "id": "", "apply_url": "",
                   "status": "", "answers": [], "resume_lines": [], "variant": ""}
            apps.append(cur)
            section = None
            continue
        if cur is None:
            continue
        h = re.match(r"^#{1,4}\s*(.*)$", raw.strip())
        if h:
            title = _unmd(h.group(1)).lower()
            if title.startswith("answers"):
                section = "answers"
            elif title.startswith("resume"):
                section = "resume"
                v = re.search(r"base:\s*(\w+)", title)
                cur["variant"] = v.group(1) if v else ""
            continue
        if section is None:
            for key, pat in (("id", r"^ID:\s*(.+)$"), ("apply_url", r"^Apply:\s*(\S+)"), ("status", r"^Status:\s*(\S+)")):
                mm = re.match(pat, line)
                if mm:
                    cur[key] = mm.group(1).strip().strip("<>")
        elif section == "answers":
            if line.startswith("Q:"):
                label = line[2:].strip()
                req = label.endswith("(required)")
                cur["answers"].append({"label": label.removesuffix("(required)").strip(), "answer": "",
                                       "required": req})
            elif line.startswith("Options:"):
                continue
            elif line.startswith("A:") and cur["answers"]:
                cur["answers"][-1]["answer"] = line[2:].strip()
            elif cur["answers"]:                       # continuation line of a multi-line answer
                prev = cur["answers"][-1]
                prev["answer"] = (prev["answer"] + "\n" + line).strip()
        elif section == "resume":
            cur["resume_lines"].append(line)
    for a in apps:
        for q in a["answers"]:
            ans = q["answer"]
            ans = re.sub(r"\s*\[voluntary EEO question - declined\]$", "", ans)
            q["from_profile"] = ans.strip() == PROFILE_MARK
            q["answer"] = "" if ans in ("(blank)", PROFILE_MARK) else ans
            q["missing"] = q["required"] and not q["from_profile"] and ans.strip() in ("", "???")
        a["resume_text"] = "\n".join(a.pop("resume_lines"))
    return apps


def resume_text(r: dict) -> str:
    """Tailored resume JSON (resume_pdf format) -> the section text used in the review Doc."""
    lines = []
    if r.get("objective"):
        lines += ["Objective", r["objective"]]
    if r.get("education"):
        lines.append("Education")
        lines += [f"{e['text']} {e['dates']}".strip() for e in r["education"]]
    if r.get("skills"):
        lines.append("Skills")
        lines += [f"{g['label']} {g['items']}" for g in r["skills"]]
    if r.get("experience"):
        lines.append("Experience")
        for x in r["experience"]:
            lines += [x["title"], f"{x['company']} {x['dates']}", *x["bullets"]]
    return "\n".join(lines)


def contact_parts(contact_line: str, email: str = "") -> dict:
    """Resume contact line ("phone • email • address • linkedin") -> fields, with the email
    optionally replaced (applications use the inbox Sourcer reads)."""
    parts = [p.strip() for p in re.split(r"\s*[•|]\s*", contact_line) if p.strip()]
    out = {"phone": next((p for p in parts if re.search(r"\d{3}.*\d{4}", p) and "@" not in p), ""),
           "email": email or next((p for p in parts if "@" in p), ""),
           "linkedin": next((p for p in parts if "linkedin" in p.lower()), "")}
    if out["linkedin"] and not out["linkedin"].startswith("http"):
        out["linkedin"] = "https://www." + out["linkedin"].removeprefix("www.")
    out["line"] = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", out["email"], contact_line) if email else contact_line
    return out


def save_queue(path: str | Path, cands: list[Candidate]) -> None:
    Path(path).write_text(json.dumps([asdict(c) for c in cands], indent=1, ensure_ascii=False))


def load_queue(path: str | Path) -> list[Candidate]:
    return [Candidate(**d) for d in json.loads(Path(path).read_text())]
