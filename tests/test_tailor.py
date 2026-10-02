import json
from pathlib import Path

import pytest

from sourcer import llm, tailor
from sourcer.models import Job
from sourcer.resume import Bank, base_resume, numbers, pick_variant, render_html
from sourcer.sources.postings import parse_greenhouse_questions, questions_from_text

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def bank():
    return Bank.load(FIX / "resume.yaml")


JOB = Job(company="Fareloop", title="Senior Data Scientist, Pricing", url="https://x", source="greenhouse",
          external_id="101", description="Dynamic pricing and personalization for airlines.")


def test_numbers_normalization():
    assert numbers("cut 4.8% and ~$8M, 90,000 units, ~$300K") == {"4.8%", "8m", "90000", "300k"}


def test_pick_variant():
    assert pick_variant("Machine Learning Engineer", "") == "mle"
    assert pick_variant("Product Data Scientist", "") == "product"
    assert pick_variant("Data Scientist", "causal modeling and forecasting") == "ds"
    assert pick_variant("Data Scientist", "deploy production inference pipelines, mlops, serving "
                        "infrastructure, latency") == "mle"


def test_base_resume_respects_variant(bank):
    mle = base_resume(bank, "mle")
    role, title, bullets = mle.roles[0]
    assert title == "ML Engineer, Pricing"
    assert bullets[0].startswith("Rewrote automated")  # mle order + alt phrasing
    assert "Tableau" not in mle.skills["Tools"]
    html = render_html(bank, mle)
    assert "Jordan Example" in html and "ML Engineer, Pricing" in html


def test_apply_tailoring_guardrails(bank):
    proposal = {
        "summary": "Data scientist who drove 12% revenue growth.",          # 12% is invented
        "skills": {"Frameworks": ["CausalML", "PyTorch", "Kubernetes"], "Tools": []},
        "roles": {"acme": {"title": "Data Scientist, Pricing", "bullets": [
            {"source_id": "ac-pricing", "text": "Shipped dynamic pricing that cut time to lease by 4.8% and saved ~$8M annually."},
            {"source_id": "ac-recs", "text": "Built a personalization engine saving ~$900K per year."},  # wrong number
            {"source_id": "ac-pricing", "text": "duplicate"},
            {"source_id": "nope", "text": "made up"},
        ]}},
        "changes": ["Led with pricing"], "gaps": ["No airline-specific NDC experience"],
    }
    t = tailor.apply_tailoring(bank, "ds", proposal)
    assert t.resume.summary == bank.summaries["ds"]
    assert t.resume.skills["Frameworks"] == ["CausalML", "PyTorch"]
    assert t.resume.skills["Tools"] == ["Tableau"]  # empty -> base
    bullets = t.resume.roles[0][2]
    assert bullets[0].startswith("Shipped dynamic pricing")  # truthful rephrase kept
    assert bullets[1] == bank.roles[0].bullet("ac-recs").text  # fabricated number reverted
    assert len(bullets) == 2
    assert any("ac-recs" in w for w in t.warnings) and any("summary" in w for w in t.warnings)


def test_tailor_resume_schema_and_fallback(bank, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    seen = {}

    def fake_ask(client, prompt, schema, **kw):
        seen["schema"], seen["prompt"], seen["kw"] = schema, prompt, kw
        return None  # simulate API failure

    monkeypatch.setattr(llm, "_ask", fake_ask)
    t = tailor.tailor_resume(bank, JOB, client=object())
    json.dumps(seen["schema"])
    role_schema = seen["schema"]["properties"]["roles"]["properties"]["acme"]
    assert role_schema["properties"]["bullets"]["items"]["properties"]["source_id"]["enum"] == \
        ["ac-pricing", "ac-recs", "ac-infra"]
    assert "<posting>" in seen["prompt"] and "555-0100" not in seen["prompt"]  # no contact info sent
    assert t.resume.summary == bank.summaries["ds"] and t.warnings


def test_greenhouse_questions_and_answers(bank, monkeypatch, tmp_path):
    data = json.loads((FIX / "greenhouse_job_questions.json").read_text())
    qs = parse_greenhouse_questions(data)
    assert [q.label for q in qs][-1] == "What gender do you identify as?"
    assert all("Veteran" not in q.label for q in qs)  # compliance section excluded

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    captured = {}

    def fake_ask(client, prompt, schema, **kw):
        captured["prompt"] = prompt
        return {"answers": [
            {"index": 3, "answer": "No", "status": "ready"},
            {"index": 4, "answer": "Your pricing work for airlines matches my experience.", "status": "review"},
        ]}

    monkeypatch.setattr(llm, "_ask", fake_ask)
    answers = tailor.answer_questions(bank, JOB, qs, {"requires_sponsorship": "No"}, client=object())
    by = {a.question.label: a for a in answers}
    assert by["First Name"].answer == "Jordan" and by["First Name"].status == "ready"
    assert by["Resume/CV"].status == "attach"
    assert by["LinkedIn Profile"].answer == "https://linkedin.com/in/example"
    assert by["Will you now or in the future require visa sponsorship?"].answer == "No"
    assert by["What gender do you identify as?"].status == "yours"
    assert "gender" not in captured["prompt"]  # EEO never sent to the model

    t = tailor.Tailored(resume=base_resume(bank, "ds"))
    d = tailor.write_packet(tmp_path, bank, JOB, t, answers)
    md = (d / "application.md").read_text()
    assert "Why are you interested in Fareloop?" in md and "`REVIEW`" in md
    assert (d / "resume.html").exists()


def test_select_answer_must_match_option(bank, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    qs = parse_greenhouse_questions(json.loads((FIX / "greenhouse_job_questions.json").read_text()))
    monkeypatch.setattr(llm, "_ask", lambda *a, **k: {"answers": [{"index": 3, "answer": "Nope", "status": "ready"}]})
    answers = tailor.answer_questions(bank, JOB, qs, {}, client=object())
    assert answers[3].status == "needs_input"
    assert answers[4].status == "needs_input" and answers[4].answer == ""  # unanswered


def test_load_facts_prunes_empty(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("work_authorization: ''\nsalary_expectation: 180k\nstories:\n  - {title: '', text: ''}\ncustom: {}\n")
    assert tailor.load_facts(p) == {"salary_expectation": "180k"}


def test_questions_from_text():
    qs = questions_from_text("Why us?\n\nDescribe a project\nyou're proud of.")
    assert [q.label for q in qs] == ["Why us?", "Describe a project you're proud of."]
