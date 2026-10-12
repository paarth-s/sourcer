"""Keep your Google Sheet job tracker in sync with Gmail, and feed it back into sourcing.

Every daily run (when GOOGLE_* + TRACKER_SHEET_ID are set):
  1. Read the tracker tab (column order doesn't matter; headers are matched by name).
  2. Scan recent recruiting email: confirmations -> Applied (+ application date), interview
     scheduling -> First Round, rejections / closed reqs -> Rejected / Closed, referral
     notices -> Connection. Unknown applications are appended as new rows.
  3. Rows' Connection column + the people tab become warm contacts for scoring/outreach,
     roles already in the tracker are left out of the digest, and the digest gets a
     "Your tracker" section (what changed + who to follow up with).
  4. After the email goes out, new roles + leads are appended to a "Sourcer feed" tab.

Statuses only move forward (Applied -> First Round -> ...); rejections never touch rows that
cover several roles ("3 DS roles"), they add a note instead. Nothing is ever deleted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .models import normalize_company

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/Los_Angeles")

STAGES = ["", "to apply", "applied", "first round", "second round", "third round", "final round", "offer"]
TERMINAL = {"rejected", "closed", "withdrawn", "declined"}
COLUMNS = {  # field -> header words to look for (lowercase, first match wins)
    "title": ["job title", "title", "role"],
    "company": ["company"],
    "location": ["location"],
    "pay": ["pay", "salary", "comp"],
    "applied_on": ["application date", "applied"],
    "status": ["current status", "status"],
    "last_contact": ["last contact"],
    "recruiter": ["recruiter"],
    "connection": ["connection", "referral", "contact"],
    "notes": ["auto notes", "notes"],
}
NOTES_HEADER = "Auto notes"
FEED_HEADER = ["Added", "Score", "Job Title", "Company", "Location", "Link", "Why it fits",
               "Who to contact", "Suggested message"]

# --- email classification ---------------------------------------------------------------
_REJECT = re.compile(
    r"not (?:to |be )?mov(?:e|ing) forward with (?:your|you)|(?:won['’]t|will not|are not|aren['’]t) be (?:moving|"
    r"proceeding) forward|decided to (?:move|go) forward with (?:other|another)|(?:chosen|decided|elected|opted) "
    r"(?:to )?(?:move|go|proceed) forward with (?:other|another|a different)|mov(?:e|ed|ing) forward with other "
    r"candidates|regret to inform|not been selected|were not selected|decided to pursue other|pursue other "
    r"candidates|(?:position|role) has (?:now |officially )?been filled|filled (?:this|the) (?:position|role)|"
    r"no longer (?:being )?consider|unable to (?:move|offer) |(?:not|n['’]t) (?:be )?able to (?:move|proceed) forward|"
    r"elected not to move forward",
    re.I)
_CLOSED = re.compile(r"(?:requisition|req|role|position|opening)[^.]{0,40}(?:closed|cancell?ed|on hold)|"
                     r"holding on (?:any )?hiring|no longer (?:hiring|open|available)", re.I)
_INTERVIEW = re.compile(  # strong signals: count from any sender
    r"interview (?:confirmation|invitation|request|scheduled|is confirmed)|your interview time|schedule (?:a|an|your)"
    r" (?:interview|phone screen|technical)|invite you to (?:an? )?interview|move you forward to|"
    r"next round of interviews|technical (?:screen|interview) with", re.I)
_INTERVIEW_HUMAN = re.compile(  # only when a person (not an ATS no-reply) writes it
    r"schedule (?:a|some) (?:time|call|chat)|(?:would|'d) (?:love|like) to (?:schedule|set up|speak|chat|talk)|"
    r"(?:share|send) (?:me )?(?:your|some) availability|phone screen|hop on a (?:quick )?call|next round", re.I)
_REFERRAL = re.compile(r"(?:has |have )?referred you|you['’]ve been referred|submitted a referral|"
                       r"thinks you['’]?d be a great", re.I)
_APPLIED = re.compile(r"thank(?:s| you) (?:so much )?for (?:applying|your application|submitting)|"
                      r"(?:taking|took) the time to apply|"
                      r"(?:received|got) your application|application (?:has been |was )?received|"
                      r"officially applied|application confirmation|we got it", re.I)
_NOISE = re.compile(r"job alert|jobs? (?:for you|you may|recommend)|recommended jobs|new jobs? (?:match|posted)|"
                    r"newsletter|webinar|account (?:has been )?created|creating your .* account|demographic survey",
                    re.I)
_ROLE_PATTERNS = [
    r"(?:for|to) (?:the|our) (?:role of |position of )?(?P<r>[^.!\n|]{3,90}?) (?:role|position|opening|opportunity|job)\b",
    r"position of (?P<r>[^.!\n|]{3,90}?)(?:\s*-\s*\d{4,}|[.!]|$)",
    r"(?:application|applied|applying) (?:to|for) (?:the )?(?P<r>[^.!\n|]{3,90}?)(?: role| position| at |\s+-\s+|"
    r"(?=\.\s)|$)",
    r"interest in (?:the |our )?(?P<r>[^.!\n|]{3,90}?) (?:role|position|opening|opportunity)",
    r"interest in (?P<r>[^.!\n|]{3,90}?) at [A-Z]",
]
_ATS_DOMAINS = ("ashbyhq.com", "greenhouse-mail.io", "greenhouse.io", "lever.co", "myworkday.com",
                "successfactors.com", "icims.com", "smartrecruiters.com", "workable.com", "avature.net",
                "jobvite.com", "paylocity.com", "bamboohr.com", "rippling.com", "taleo.net", "oraclecloud.com")
_GENERIC_DOMAINS = {"gmail", "yahoo", "outlook", "hotmail", "icloud", "linkedin", "indeedapply", "calendly", "google", "zoom"}
ALIASES = {"gm": "general motors", "generalmotors": "general motors", "pg&e": "pge", "gapinc": "gap",
           "dd": "doordash", "jpmc": "jpmorgan chase", "stanfordhealthcare": "stanford",
           "fb": "meta", "facebook": "meta", "facebookmail": "meta", "playstation": "sony"}


@dataclass
class Event:
    kind: str                     # applied | interview | rejected | closed | referral
    company: str                  # as found in the email ("" if unknown)
    role: str
    when: datetime
    sender: str
    msg_id: str
    human: bool = False           # written by a person (not a no-reply ATS)
    referrer: str = ""


@dataclass
class Row:
    n: int                        # 1-based sheet row number
    cells: dict[str, str]

    def get(self, f: str) -> str:
        return (self.cells.get(f) or "").strip()

    @property
    def stage(self) -> str:
        return self.get("status").lower()

    @property
    def umbrella(self) -> bool:
        """'3 DS roles', '4 roles (1 DS / 3 MLE)', 'DS, ML (2 roles)' - one row for several applications."""
        return bool(re.search(r"\d+\s*\w*\s*roles?\b|\broles\b", self.get("title"), re.I))


@dataclass
class TrackerResult:
    rows: list[Row] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    to_apply: list[str] = field(default_factory=list)
    warm_contacts: dict[str, list[str]] = field(default_factory=dict)
    events: int = 0


def _addr(sender: str) -> tuple[str, str]:
    m = re.search(r"([\w.+'-]+)@([\w.-]+)", sender or "")
    return (m.group(1).lower(), m.group(2).lower()) if m else ("", "")


def _domain_company(sender: str) -> str:
    """'salesforce@myworkday.com' -> 'salesforce'; 'jane@wing.com' -> 'wing'; ATS no-reply -> ''."""
    local, dom = _addr(sender)
    if not dom:
        return ""
    if local.startswith("indeedapply"):
        return ""                               # Indeed Apply relays other companies' applications
    if dom.endswith("myworkday.com") or dom.endswith("icims.com"):
        return re.sub(r"\+.*$", "", local)      # tenant name, e.g. amd+autoreply -> amd
    if any(dom.endswith(a) for a in _ATS_DOMAINS):
        return ""
    if dom.endswith(".edu"):
        return ""
    parts = dom.split(".")
    root = parts[-2] if len(parts) >= 2 else parts[0]
    if root in ("co", "com") and len(parts) >= 3:
        root = parts[-3]
    return "" if root in _GENERIC_DOMAINS else root


def _is_human(sender: str) -> bool:
    local, dom = _addr(sender)
    return bool(local) and not re.search(r"no-?reply|donotreply|do-not-reply|notification|system|jobs|careers|"
                                         r"talent|recruiting|autoreply|mail|hello|info|scheduling|team|hiring|"
                                         r"^hr$|apply", local) \
        and not any(dom.endswith(a) for a in _ATS_DOMAINS)


def _clean_role(r: str) -> str:
    r = re.sub(r"^(?:the|our|a|an)\s+", "", r.strip(" -:,'\"“”"), flags=re.I)
    r = re.sub(r"^(?:R-?)?\d[\d-]*\s*[-–:]\s*", "", r)           # leading requisition number
    r = re.sub(r"\s*[-–]\s*(?:R-?)?\d{4,}.*$|\s*\(?(?:ref(?:erence)? id|req(?:uisition)? id)[^)]*\)?", "", r, flags=re.I)
    return r.strip(" -:,")[:90]


def classify(msg: dict) -> Event | None:
    subject, body = msg.get("subject", ""), msg.get("body", "")
    text = f"{subject}\n{body[:4000]}"
    if _NOISE.search(subject) or not text.strip():
        return None
    if _REJECT.search(text):
        kind = "rejected"
    elif _CLOSED.search(text):
        kind = "closed"
    elif _REFERRAL.search(text):
        kind = "referral"
    elif _INTERVIEW.search(text) or (_is_human(msg.get("sender", "")) and _INTERVIEW_HUMAN.search(text)):
        kind = "interview"
    elif _APPLIED.search(text):
        kind = "applied"
    else:
        return None
    role = ""
    for pat in _ROLE_PATTERNS:
        m = re.search(pat, f"{subject}. {body[:1500]}", re.I)
        if m:
            role = _clean_role(m.group("r"))
            if role and not re.match(r"(?:this|that|your|any|one|a|career|team|company|us|following|joining|open|all|these|other)\b",
                                     role, re.I):
                break
            role = ""
    referrer = ""
    if kind == "referral":
        m = re.search(r"([A-Z][a-z]+(?: [A-Z][\w'-]+){1,2}) (?:has referred|referred you|thinks you|has shared)", text)
        referrer = m.group(1) if m else ""
    when = datetime.fromtimestamp(msg.get("date_ms", 0) / 1000, tz=timezone.utc)
    return Event(kind=kind, company=_domain_company(msg.get("sender", "")), role=role, when=when,
                 sender=msg.get("sender", ""), msg_id=msg.get("id", ""), human=_is_human(msg.get("sender", "")),
                 referrer=referrer)


# --- matching ---------------------------------------------------------------------------
def company_key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]", "", normalize_company((name or "").replace("&", "")).replace(" ", ""))
    for suf in ("hiringfast", "inc", "llc", "llp", "us", "global", "group", "financial", "health", "ai", "labs"):
        if k.endswith(suf) and len(k) > len(suf) + 2:
            k = k[: -len(suf)]
    return re.sub(r"[^a-z0-9]", "", normalize_company(ALIASES.get(k, k)))


def find_company(ev: Event, msg: dict, known: dict[str, str]) -> str:
    """The tracker company this email is about (a key of `known`), or a best guess for new rows."""
    if ev.company:
        k = company_key(ev.company)
        if k in known:
            return k
    subject, body = msg.get("subject", ""), msg.get("body", "")[:800]
    best: tuple[int, int, str] | None = None
    for k, display in known.items():
        names = {display.split("(")[0].strip()} | {a for a, v in ALIASES.items() if company_key(v) == k and len(a) > 3}
        for name in names:
            if len(name) < 2:
                continue
            pat = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.I)
            for prio, text in ((0, subject), (1, body)):
                # case-insensitive, but the hit must be capitalized ("Wing", not "wing")
                m = next((m for m in pat.finditer(text) if not m.group()[0].islower()), None)
                if m:
                    cand = (prio, m.start(), k)
                    best = cand if best is None or cand < best else best
                    break
    if best:
        return best[2]
    # Not in the tracker yet: name it from the email (and remember the display name for new rows)
    word = r"[A-Z][\w&'-]*(?:\.(?:com|ai|io|co))?"
    pat = re.compile(rf"(?:applying|applied|application|apply) (?:to|at|with) ({word}(?: {word}){{0,2}})"
                     rf"|interest in (?:joining )?({word}(?: {word}){{0,2}})|(?:position|role) at ({word}(?: {word}){{0,2}})")
    m = pat.search(subject) or pat.search(body)
    name = next((g for g in m.groups() if g), "") if m else ""
    name = re.sub(r"\s+(?:Hi|Hello|Dear|We|Our|Thank|Thanks)\b.*$", "", name)
    name = re.sub(r"\s+(?:Inc|LLC|LLP|Team|for|and|the)\.?$", "", name).strip() or ev.company.title()
    k = company_key(name)
    if k:
        known.setdefault(k, name)
    return k


_ABBR = {"ds": "data scientist", "mle": "machine learning engineer", "ml": "machine learning", "sr": "senior",
         "de": "data engineer", "mts": "member of technical staff", "ai": "ai"}
_STOP = {"the", "a", "an", "of", "and", "for", "at", "role", "roles", "position", "ii", "iii", "i", "-", "&"}


def _tokens(title: str) -> set[str]:
    t = title.lower()
    for a, full in _ABBR.items():
        t = re.sub(rf"\b{a}\b", full, t)
    return {w for w in re.findall(r"[a-z]+", t) if w not in _STOP}


def title_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0.0


def match_row(rows: list[Row], key: str, role: str) -> tuple[Row | None, bool]:
    """-> (row, confident). Not confident = several candidate rows and the role doesn't pick one."""
    cands = [r for r in rows if company_key(r.get("company")) == key]
    if not cands:
        return None, False
    if role:
        scored = sorted(((title_similarity(role, r.get("title")), r.n, r) for r in cands), key=lambda x: x[:2],
                        reverse=True)
        if scored[0][0] >= 0.75:
            return scored[0][2], True
    elif len(cands) == 1:
        return cands[0], True
    umb = [r for r in cands if r.umbrella]
    return (umb[-1] if umb else cands[-1]), False


# --- applying events -------------------------------------------------------------------
def _fmt(d: datetime | date) -> str:
    d = d.astimezone(TZ).date() if isinstance(d, datetime) else d
    return f"{d.month}/{d.day}/{d.year}"


def _parse(s: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _rank(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else (1 if not stage else 99)


def apply_events(rows: list[Row], events: list[tuple[Event, str]], *, display: dict[str, str]
                 ) -> tuple[dict[int, dict[str, str]], list[dict[str, str]], list[str]]:
    """Fold email events into the tracker. Returns (cell edits by row, new rows, change log)."""
    edits: dict[int, dict[str, str]] = {}
    new: list[dict[str, str]] = []
    log_: list[str] = []

    def setc(row: Row, f: str, v: str):
        if row.get(f) != v:
            row.cells[f] = v
            if row.n > 0:
                edits.setdefault(row.n, {})[f] = v

    def note(row: Row, text: str):
        cur = row.get("notes")
        if text not in cur:
            setc(row, "notes", (cur + "; " if cur else "") + text)

    for ev, key in sorted(events, key=lambda e: e[0].when):
        day = _fmt(ev.when)
        if ev.role and key and company_key(ev.role) == key:
            ev.role = ""                        # "Thanks for applying to Nubank" - that's the company
        row, confident = match_row(rows, key, ev.role) if key else (None, False)
        name = display.get(key) or ev.company.title() or "?"
        role = ev.role or (row.get("title") if row else "")
        if row is None:
            if ev.kind in ("applied", "interview", "rejected", "closed") and key:
                status = {"applied": "Applied", "interview": "First Round", "rejected": "Rejected",
                          "closed": "Closed"}[ev.kind]
                cells = {"title": ev.role or "?", "company": name, "applied_on": day if ev.kind == "applied" else "",
                         "status": status, "last_contact": day, "notes": f"{day}: added from Gmail ({ev.kind})"}
                r = Row(n=-(len(new) + 1), cells=cells)
                rows.append(r)
                new.append(cells)
                display[key] = name
                log_.append(f"New row: {ev.role or 'role'} @ {name} ({status})")
            continue
        stage, was = row.stage, row.get("status") or "blank"
        last = _parse(row.get("last_contact"))
        if ev.kind == "applied":
            if not confident and ev.role and not row.umbrella:
                cells = {"title": ev.role, "company": name, "applied_on": day, "status": "Applied",
                         "last_contact": day, "connection": row.get("connection"),
                         "notes": f"{day}: added from Gmail (applied)"}
                r = Row(n=-(len(new) + 1), cells=cells)
                rows.append(r)
                new.append(cells)
                log_.append(f"New row: {ev.role} @ {name} (Applied)")
                continue
            if not row.get("applied_on") and (confident or row.umbrella):
                setc(row, "applied_on", day)
            if stage in ("", "to apply"):
                setc(row, "status", "Applied")
                log_.append(f"{row.get('title')} @ {name}: {was} -> Applied")
        elif ev.kind == "referral":
            if ev.referrer and ev.referrer.split()[0].lower() not in row.get("connection").lower():
                setc(row, "connection", (row.get("connection") + ", " if row.get("connection") else "") + ev.referrer)
            note(row, f"{day}: referral confirmed{' (' + ev.referrer + ')' if ev.referrer else ''}")
        elif ev.kind == "interview":
            if _rank(stage) < _rank("first round") and stage not in TERMINAL:
                setc(row, "status", "First Round")
                log_.append(f"{row.get('title')} @ {name}: {was} -> First Round (interview email {day})")
            if ev.human and not row.get("recruiter"):
                setc(row, "recruiter", ev.sender.replace('"', "")[:80])
        elif ev.kind in ("rejected", "closed"):
            label = "Rejected" if ev.kind == "rejected" else "Closed"
            newer_manual = last and last > ev.when.astimezone(TZ).date()
            if stage in TERMINAL:
                pass
            elif row.umbrella or not confident or newer_manual:
                if stage in ("", "to apply"):
                    setc(row, "status", "Applied")      # a rejection means you did apply
                note(row, f"{day}: {label.lower()} - {role or 'one role'}")
                log_.append(f"{name}: {label.lower()} for {role or 'a role'} (noted - status unchanged since the "
                            f"row covers other roles too)")
            else:
                setc(row, "status", label)
                log_.append(f"{row.get('title')} @ {name}: {was} -> {label} ({day})")
        if not last or ev.when.astimezone(TZ).date() > last:
            setc(row, "last_contact", day)
    return edits, new, log_


# --- follow-ups + warm contacts ----------------------------------------------------------
def followups(rows: list[Row], today: date) -> tuple[list[str], list[str]]:
    out, to_apply = [], []
    for r in rows:
        stage, who = r.stage, r.get("connection")
        title, comp = r.get("title") or "role", r.get("company")
        last = _parse(r.get("last_contact")) or _parse(r.get("applied_on"))
        days = (today - last).days if last else None
        if stage in ("first round", "second round", "third round", "final round") and (days is None or days >= 6):
            rec = r.get("recruiter")
            out.append(f"{title} @ {comp} ({r.get('status')}): "
                       + (f"no contact in {days} days - " if days is not None else "")
                       + f"send a check-in{' to ' + rec if rec else ' to your recruiter'}.")
        elif stage == "applied" and who and days is not None and 7 <= days <= 30:
            out.append(f"{title} @ {comp}: applied {days} days ago, no news - ask {who} to nudge the hiring team.")
        elif stage == "to apply":
            to_apply.append(f"{title} @ {comp}" + (f" (via {who})" if who else ""))
    return out, to_apply


def warm_contacts(rows: list[Row], people: list[list[str]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        who, comp = r.get("connection"), r.get("company").split("(")[0].strip()
        if who and comp and not re.fullmatch(r"\w+ alum(?:ni|nus|na)?", who, re.I):
            out.setdefault(comp, [])
            if all(p["name"] != who for p in out[comp]):
                out[comp].append({"name": who, "position": "connection from your tracker"})
    for p in people[1:]:
        if len(p) >= 2 and p[0].strip() and p[1].strip():
            comp = ALIASES.get(p[1].strip().lower(), p[1].strip())
            out.setdefault(comp, []).append({"name": p[0].strip(), "position": "connection from your tracker"})
    return out


def already_tracked(company: str, title: str, rows: list[Row]) -> bool:
    k = company_key(company)
    return any(company_key(r.get("company")) == k and title_similarity(title, r.get("title")) >= 0.75
               for r in rows if not r.umbrella)


# --- sheet I/O ------------------------------------------------------------------------------
def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def read_rows(values: list[list[str]]) -> tuple[dict[str, int], list[Row], int]:
    """-> (field -> column index, rows, header row index)."""
    hdr_i = next((i for i, v in enumerate(values[:5]) if any("company" in (c or "").lower() for c in v)), 0)
    header = [(c or "").strip().lower() for c in (values[hdr_i] if values else [])]
    cols: dict[str, int] = {}
    for f, words in COLUMNS.items():
        for w in words:
            idx = next((i for i, h in enumerate(header) if w in h and i not in cols.values()), None)
            if idx is not None:
                cols[f] = idx
                break
    rows = []
    for i, v in enumerate(values[hdr_i + 1:], start=hdr_i + 2):
        cells = {f: (v[c] if c < len(v) else "") for f, c in cols.items()}
        if any((x or "").strip() for x in cells.values()):
            rows.append(Row(n=i, cells=cells))
    return cols, rows, hdr_i


def sync(g, sheet_id: str, cfg_tracker: dict, store, *, write: bool = True, now: datetime | None = None
         ) -> TrackerResult:
    now = now or datetime.now(timezone.utc)
    tab = cfg_tracker.get("tab", "Main Arena")
    values = g.sheet_values(sheet_id, f"'{tab}'!A1:Z2000")
    cols, rows, hdr_i = read_rows(values)
    res = TrackerResult(rows=rows)
    if "company" not in cols or "status" not in cols:
        raise RuntimeError(f"tracker tab {tab!r} needs Company and Status columns")
    if write and "notes" not in cols:
        cols["notes"] = max(len(values[hdr_i]) if values else 0, max(cols.values()) + 1)
        g.sheet_update(sheet_id, [(f"'{tab}'!{_col(cols['notes'])}{hdr_i + 1}", [[NOTES_HEADER]])])

    last_sync = store.get_meta("tracker_last_sync")
    days = cfg_tracker.get("first_lookback_days", 150) if not last_sync else \
        max(2, (now - datetime.fromisoformat(last_sync)).days + 2)
    q = (f"newer_than:{days}d -category:promotions -category:social -from:me "
         "(application OR applying OR applied OR interview OR referred OR candidacy OR \"your interest\" OR position "
         "OR role OR opportunity OR candidates)")
    seen = set(filter(None, (store.get_meta("tracker_seen_ids") or "").split(",")))
    display = {company_key(r.get("company")): r.get("company").split("(")[0].strip() for r in rows if r.get("company")}
    events: list[tuple[Event, str]] = []
    for mid in g.gmail_search(q, limit=cfg_tracker.get("max_messages", 400)):
        if mid in seen:
            continue
        msg = g.gmail_message(mid)
        ev = classify(msg)
        if ev:
            events.append((ev, find_company(ev, msg, display)))
        seen.add(mid)
    res.events = len(events)
    edits, new, res.changes = apply_events(rows, events, display=display)

    if write:
        upd = [(f"'{tab}'!{_col(cols[f])}{n}", [[v]]) for n, fs in edits.items() for f, v in fs.items() if f in cols]
        g.sheet_update(sheet_id, upd)
        width = max(cols.values()) + 1
        g.sheet_append(sheet_id, f"'{tab}'!A{hdr_i + 1}",
                       [[cells.get(next((f for f, c in cols.items() if c == i), ""), "") for i in range(width)]
                        for cells in new])
        store.set_meta("tracker_last_sync", now.isoformat())
        store.set_meta("tracker_seen_ids", ",".join(sorted(seen))[-200000:])

    people = []
    if cfg_tracker.get("people_tab"):
        try:
            people = g.sheet_values(sheet_id, f"'{cfg_tracker['people_tab']}'!A1:D500")
        except Exception as e:  # noqa: BLE001 - the people tab is optional
            log.warning("tracker people tab unreadable: %s", type(e).__name__)
    res.warm_contacts = warm_contacts(rows, people)
    res.followups, res.to_apply = followups(rows, now.astimezone(TZ).date())
    return res


def append_feed(g, sheet_id: str, cfg_tracker: dict, run_result, now: datetime | None = None) -> int:
    """Append today's roles + leads to the feed tab (created on first use)."""
    tab = cfg_tracker.get("feed_tab", "Sourcer feed")
    if tab not in g.sheet_tabs(sheet_id):
        g.sheet_add_tab(sheet_id, tab)
        g.sheet_update(sheet_id, [(f"'{tab}'!A1", [FEED_HEADER])])
    day = _fmt(now or datetime.now(timezone.utc))
    rows = []
    for sj in run_result.new_jobs:
        c = sj.contacts[0] if sj.contacts else None
        who = (f"{c.name} ({c.title}) {c.email or c.linkedin_url or c.search_url}" if c else
               ", ".join(f"{x.name} ({x.position})" for x in sj.connections[:2]))
        rows.append([day, round(sj.score), sj.job.title, sj.job.company, sj.job.location, sj.job.url,
                     "; ".join(sj.reasons[:3]), who, (c.email_body or c.message) if c else ""])
    for lead in run_result.leads:
        c = lead.contacts[0] if lead.contacts else None
        rows.append([day, "", "(no posting yet - reach out)", lead.company, "",
                     lead.funding.url if lead.funding else "", "; ".join(lead.reasons[:3]),
                     f"{c.name} ({c.title}) {c.email or c.linkedin_url or c.search_url}" if c else "",
                     (c.email_body or c.message) if c else ""])
    g.sheet_append(sheet_id, f"'{tab}'!A1", rows)
    return len(rows)
