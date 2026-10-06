from types import SimpleNamespace as NS

from sourcer import llm, outreach
from sourcer.models import Connection
from sourcer.outreach import Contact, Target


def _resp(blocks, stop="tool_use"):
    return NS(content=blocks, stop_reason=stop)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def _search_result(*urls):
    return NS(type="web_search_tool_result", content=[NS(url=u) for u in urls])


def _fetch_result(url, text):
    return NS(type="web_fetch_tool_result",
              content=NS(url=url, content=NS(source=NS(data=text))))


def _report(people):
    return NS(type="tool_use", name="report_people", input={"people": people})


def person(name, li="", email="", src=""):
    return {"name": name, "title": "Head of Data", "why": "hiring manager",
            "linkedin_url": li, "email": email, "email_source_url": src}


def test_find_people_keeps_only_verified_links_and_emails():
    client = FakeClient([
        _resp([_search_result("https://www.linkedin.com/in/jane-doe/", "https://acme.com/team")],
              stop="pause_turn"),
        _resp([_fetch_result("https://acme.com/team", "Reach our founder at founder@acme.com"),
               _report([
                   person("Jane Doe", li="https://linkedin.com/in/jane-doe"),           # seen -> kept
                   person("Bob Ghost", li="https://linkedin.com/in/bob-made-up"),       # not seen -> dropped
                   person("Ann Founder", email="founder@acme.com", src="https://acme.com/team"),
                   person("Guess Who", email="guess@acme.com"),                         # not on page
               ])]),
    ])
    t = Target(company="Acme", role_title="Data Scientist", role_url="https://x")
    got = outreach.find_people(client, t, logged=[], max_people=4)
    by = {c.name: c for c in got}
    assert by["Jane Doe"].linkedin_url and by["Jane Doe"].verified
    assert not by["Bob Ghost"].linkedin_url and not by["Bob Ghost"].verified
    assert by["Ann Founder"].email == "founder@acme.com" and by["Ann Founder"].channel == "email"
    assert by["Guess Who"].email == ""
    assert len(client.calls) == 2  # resumed after pause_turn
    tools = {t.get("name") for t in client.calls[0]["tools"]}
    assert {"web_search", "web_fetch", "report_people"} <= tools


def test_find_people_skips_already_contacted():
    client = FakeClient([_resp([_report([person("Jane Doe"), person("New Person")])])])
    got = outreach.find_people(client, Target(company="Acme"),
                               logged=[{"name": "Jane Doe", "company": "Acme Inc"}])
    assert [c.name for c in got] == ["New Person"]


def test_draft_messages_and_note_limit(monkeypatch):
    t = Target(company="Acme", role_title="DS", role_url="https://x",
               contacts=[Contact("Jane Doe", "Head of Data", "hm"),
                         Contact("Ann F", "CEO", "founder", email="ann@acme.com", verified=True)])
    seen = {}

    def fake_ask(client, prompt, schema, **kw):
        seen["prompt"] = prompt
        return {"drafts": [
            {"index": 0, "connection_note": "x" * 400, "message": "Hi Jane...", "email_subject": "s",
             "email_body": "should be ignored"},
            {"index": 1, "connection_note": "", "message": "", "email_subject": "Data at Acme",
             "email_body": "Hi Ann..."},
            {"index": 7, "connection_note": "bad", "message": "", "email_subject": "", "email_body": ""},
        ]}

    monkeypatch.setattr(llm, "_ask", fake_ask)
    outreach.draft_messages(object(), t, "candidate text", "my old note", "Paarth")
    jane, ann = t.contacts
    assert len(jane.connection_note) == 300 and jane.message == "Hi Jane..." and jane.email_body == ""
    assert ann.email_subject == "Data at Acme" and ann.email_body == "Hi Ann..."
    assert "my old note" in seen["prompt"] and "https://x" in seen["prompt"]


def test_prepare_puts_known_connection_first(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(llm, "_client", lambda: object())
    monkeypatch.setattr(llm, "candidate_text", lambda p: "cand")
    monkeypatch.setattr(outreach, "find_people",
                        lambda c, t, logged, max_people=3: [Contact("A", "Recruiter", "r"),
                                                            Contact("B", "HM", "h"), Contact("C", "x", "y")])
    monkeypatch.setattr(outreach, "draft_messages", lambda *a, **k: None)
    t = Target(company="Acme", known=[Connection("Sam", "Lee", "Acme", "PM", url="https://linkedin.com/in/sam")])
    outreach.prepare([t], tmp_path, {"name": "Paarth"})
    assert [c.name for c in t.contacts] == ["Sam Lee", "A", "B"] and t.contacts[0].first_degree


def test_email_renders_contacts():
    from datetime import datetime, timezone
    from sourcer.email_html import render_email
    from sourcer.models import Job, ScoredJob
    from sourcer.pipeline import RunResult
    job = Job(company="Acme", title="Data Scientist", url="https://x", source="greenhouse", external_id="1")
    c1 = Contact("Jane Doe", "Head of Data", "hiring manager", linkedin_url="https://linkedin.com/in/jd",
                 verified=True, connection_note="Hi Jane - note", message="Hi Jane - message")
    c2 = Contact("Ann F", "CEO", "founder", email="ann@acme.com", verified=True,
                 email_subject="Data at Acme", email_body="Hi Ann - email")
    c3 = Contact("Unverified Person", "Recruiter", "recruiter", message="Hi")
    res = RunResult(new_jobs=[ScoredJob(job=job, score=80, contacts=[c1, c2, c3])])
    _, html, text = render_email(res, now=datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert "Hi Jane - note" in html and "Hi Jane - message" in html
    assert "mailto:ann@acme.com" in html and "Email subject: Data at Acme" in html
    assert "profile not confirmed" in html
    assert "Hi Ann - email" in text


def test_template_fallback_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "resume.yaml").write_text(
        "highlights:\n"
        "  - {keywords: [recommendation, ranking], short: built a recommender (+27%), long: I built a recommender.}\n"
        "  - {keywords: [pricing], short: built pricing (~$8M), long: I built pricing.}\n")
    role = Target(company="Zillow", role_title="Senior Data Scientist", role_url="https://z",
                  role_summary="Pricing models for rentals")
    lead = Target(company="OuterSignal", context="raises $22M for personalization ranking")
    friend = Target(company="Acme", role_title="Product Analyst", role_url="https://a",
                    known=[Connection("Sam", "Lee", "Acme", "PM", url="https://linkedin.com/in/sam")])
    outreach.prepare([role, lead, friend], tmp_path, {"name": "Paarth"})

    assert [c.name for c in role.contacts] == ["Hiring manager", "Recruiter"]
    hm = role.contacts[0]
    assert hm.placeholder and "linkedin.com/search" in hm.search_url
    assert "built pricing (~$8M)" in hm.connection_note and len(hm.connection_note) <= 300
    assert "https://z" in hm.message and hm.message.endswith("Paarth")
    assert "flag my application" in role.contacts[1].message

    assert lead.contacts[0].name == "Founder / CEO" and "recommender" in lead.contacts[0].connection_note

    assert friend.contacts[0].name == "Sam Lee" and friend.contacts[0].first_degree
    assert friend.contacts[0].message.startswith("Hi Sam!") and "referring me" in friend.contacts[0].message
    assert len(friend.contacts) == 3
