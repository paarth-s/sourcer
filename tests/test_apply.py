import html
import re

from sourcer import apply as ap
from sourcer.forms import norm

FACTS = {"contact": {"name": "Jane Doe", "email": "jane@example.edu", "phone": "(555) 000-0000",
                     "linkedin": "https://www.linkedin.com/in/jane"},
         "current_location": "San Francisco, CA", "work_authorized": True, "requires_sponsorship": False,
         "salary_expectation": "$140,000 - $170,000", "how_did_you_hear": "Company careers page",
         "earliest_start": "2 weeks from offer"}


def role(**kw):
    r = {"uid": "greenhouse:1", "company": "Acme", "title": "Data Scientist", "url": "https://x", "location":
         "San Francisco, CA", "source": "greenhouse", "board": "acme", "external_id": "1", "score": 60,
         "reasons": [], "connections": [], "required_years": 2, "description": "Ship models to production."}
    r.update(kw)
    return r


def test_fit_rules():
    assert ap.is_fit(role())[0]
    assert ap.is_fit(role(title="Machine Learning Engineer, Ranking"))[0]
    assert ap.is_fit(role(title="Applied AI Engineer", location="Palo Alto, CA"))[0]
    assert not ap.is_fit(role(connections=["A Friend"]))[0]              # warm path -> outreach, not cold apply
    assert not ap.is_fit(role(location="New York, NY"))[0]
    assert not ap.is_fit(role(location="Remote - US"))[0]
    assert not ap.is_fit(role(title="Staff Data Scientist"))[0]
    assert not ap.is_fit(role(title="Data Analyst"))[0]
    assert not ap.is_fit(role(title="Master's University Grad Data Scientist (USA)"))[0]
    assert not ap.is_fit(role(title="Software Engineer, Payments"))[0]   # not DS/ML/AI
    assert not ap.is_fit(role(required_years=6))[0]
    assert not ap.is_fit(role(title="Senior Data Scientist", required_years=None))[0]
    assert ap.is_fit(role(title="Senior Data Scientist", required_years=3))[0]
    assert not ap.is_fit(role(source="workday"))[0]


def test_select_ranks_applied_caps_per_company_and_excludes():
    roles = [role(uid=f"greenhouse:{i}", external_id=str(i), score=50 + i) for i in range(4)]
    roles.append(role(uid="lever:r", company="Beta", source="lever", url="https://jobs.lever.co/beta/r",
                      title="Research Scientist, Machine Learning", description="PhD required", score=66))
    picked, _ = ap.select(roles, exclude={"greenhouse:3"}, limit=15)
    assert [c.id for c in picked] == ["greenhouse:2", "greenhouse:1", "lever:r"]   # 2 per company, research ranked down
    assert picked[0].apply_url == "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=2"
    assert picked[-1].apply_url == "https://jobs.lever.co/beta/r/apply"
    assert ap.pick_variant("Machine Learning Engineer") == "mle" and ap.pick_variant("Data Scientist, Growth") == "ds"


def test_auto_answers():
    a = lambda label, kind="text", opts=(): ap.auto_answer(label, kind, list(opts), FACTS, "Acme")[0]
    assert a("First Name*") == "Jane" and a("Last Name*") == "Doe" and a("Full name ✱") == "Jane Doe"
    assert a("Email*") == "jane@example.edu" and a("Phone Number") == "(555) 000-0000"
    assert a("Are you legally authorized to work in the United States?*", "combobox", ["Yes", "No"]) == "Yes"
    assert a("Will you now or in the future require sponsorship for employment visa status?", "radio",
             ["Yes", "No"]) == "No"
    assert a("Are you able to work from our Foster City, CA HQ 3 days per week?", "buttons", ["Yes", "No"]) == "Yes"
    assert a("Gender*", "combobox", ["Male", "Female", "I don't wish to answer"]) == "I don't wish to answer"
    assert a("Veteran Status", "radio", ["I am a veteran", "I decline to self-identify for protected veteran status"]) \
        .startswith("I decline")
    assert a("Gender", "combobox", ["Male", "Female"]) is None             # no decline option -> ask, never guess
    assert a("Country*", "combobox", ["United States +1", "Canada +1"]) == "United States +1"
    assert a("What is your desired salary range?") == "$140,000 - $170,000"
    assert a("Why do you want to work at Acme?*", "textarea") is None    # drafted by the agent, reviewed by you
    assert a("Resume/CV", "file") == "<resume>"


def test_contact_parts_swaps_email():
    c = ap.contact_parts("(555) 000-0000 • jane@old.edu • 1 Main St, SF, CA • linkedin.com/in/jane", "jane@new.edu")
    assert c["email"] == "jane@new.edu" and "jane@new.edu" in c["line"] and "old.edu" not in c["line"]
    assert c["linkedin"] == "https://www.linkedin.com/in/jane" and c["phone"] == "(555) 000-0000"


def _drive_text(h: str) -> str:
    """Mimic Drive's text rendering of a Google Doc made from our HTML (bold headings, escapes)."""
    h = re.sub(r"<h1>(.*?)</h1>", r"\n# **\1**\n", h)
    h = re.sub(r"<h2>(.*?)</h2>", r"\n## **\1**\n", h)
    h = re.sub(r"<h3>(.*?)</h3>", r"\n### **\1**\n", h)
    h = re.sub(r"<[^>]+>", "", h.replace("<br>", "\n\n").replace("</p>", "\n\n"))
    h = html.unescape(h)
    return h.replace("*", "\\*").replace("\\*\\*", "**").replace("[", "\\[").replace("]", "\\]").replace("~", "\\~")


def test_review_doc_round_trip():
    c = ap.Candidate(id="greenhouse:9", company="Acme", title="ML Engineer", url="u",
                     apply_url="https://job-boards.greenhouse.io/embed/job_app?for=acme&token=9", ats="greenhouse",
                     location="SF", score=1, rank=1, why=["fits"], variant="mle", status="ready")
    c.answers = [
        {"label": "First Name*", "answer": "Jane", "how": "profile", "required": True, "options": []},
        {"label": "Why Acme?*", "answer": "I like ~$8M problems.\nSecond para.", "how": "drafted", "required": True,
         "options": [], "kind": "textarea"},
        {"label": "Sponsorship?*", "answer": "No", "how": "profile", "required": True, "options": ["Yes", "No"]},
        {"label": "Gender", "answer": "Decline to self-identify", "how": "eeo-decline", "required": False,
         "options": ["Male", "Decline to self-identify"]},
        {"label": "Salary?*", "answer": "", "how": "", "required": True, "options": [], "kind": "text"},
        {"label": "Why us?*", "answer": "", "how": "", "required": True, "options": [], "kind": "textarea"},
    ]
    doc = ap.render_doc([c], {"greenhouse:9": "Objective\nApplied ML engineer."}, "2026-10-14")
    assert "Jane" not in doc and "Gender" not in doc and "Sponsorship" not in doc   # standard fields stay out
    (app,) = ap.parse_doc(_drive_text(doc))
    assert (app["n"], app["id"]) == (1, "greenhouse:9")
    assert app["apply_url"] == "https://job-boards.greenhouse.io/acme/jobs/9" and app["status"] == "READY"
    got = {q["label"]: q for q in app["answers"]}
    assert got["Why Acme?*"]["answer"] == "I like ~$8M problems.\nSecond para."
    assert "Salary?*" not in got                          # factual questions are left to the form
    assert got["Why us?*"]["missing"]                     # an essay left for Paarth is flagged
    assert norm("Why Acme?*") == norm("Why Acme? (required)") == "why acme"


def test_doc_requests_replace_body_and_style_headings():
    paras = [("TITLE", "Sourcer applications - 2026-10-15"), ("P", "intro é"), ("H2", "1. Acme - ML"), ("P", "Q: x")]
    reqs = ap.doc_requests(paras, end_index=65)
    assert reqs[0] == {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 64}}}
    text = reqs[1]["insertText"]["text"]
    assert text == "Sourcer applications - 2026-10-15\nintro é\n1. Acme - ML\nQ: x"
    h2 = [r for r in reqs if r.get("updateParagraphStyle", {}).get("paragraphStyle", {}).get("namedStyleType") == "HEADING_2"]
    start = 1 + len("Sourcer applications - 2026-10-15\nintro é\n")
    assert h2[0]["updateParagraphStyle"]["range"] == {"startIndex": start, "endIndex": start + len("1. Acme - ML") + 1}
    assert ap.doc_requests(paras, end_index=2)[0].get("insertText")   # empty Doc: nothing to delete
