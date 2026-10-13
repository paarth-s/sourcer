import base64
from email.message import EmailMessage

import pytest

pytest.importorskip("reportlab")

from sourcer import resume_pdf as rp
from sourcer.cli import main

DOC = r"""Jane Doe

(555) 000-0000 • jane@example.com • San Francisco, CA

Objective

Data scientist focused on pricing and experimentation.

Education

MS in Statistics, Example University | Thesis: Flows 2022 - 2024

BS in Mathematics, State University 2018 - 2022

Skills

Frameworks PyTorch, Scikit-learn, Pandas

Languages Python, SQL

Experience

Data Scientist, Pricing

Acme Corp, Chicago, IL Jan 2026 - Sep 2026

Built a causal pricing system for 90,000 homes, cutting time to lease 4.8% and saving \~$8M a year.

Shipped a recommendation engine for deals, saving \~300K annually.

Analyst

Beta Inc, Chicago, IL 2024–2026

Defined a north-star KPI adopted org-wide.
"""


@pytest.fixture
def base():
    return rp.parse_text(DOC)


def test_parse_structure(base):
    assert base["name"] == "Jane Doe" and "jane@example.com" in base["contact"]
    assert [e["dates"] for e in base["education"]] == ["2022 - 2024", "2018 - 2022"]
    assert base["education"][1]["text"] == "BS in Mathematics, State University"   # "University 2018" isn't a month
    assert [g["label"] for g in base["skills"]] == ["Frameworks", "Languages"]
    acme, beta = base["experience"]
    assert (acme["title"], acme["company"], acme["dates"]) == ("Data Scientist, Pricing", "Acme Corp, Chicago, IL",
                                                               "Jan 2026 - Sep 2026")
    assert len(acme["bullets"]) == 2 and "~$8M" in acme["bullets"][0] and "\\" not in acme["bullets"][0]
    assert beta["title"] == "Analyst" and beta["bullets"] == ["Defined a north-star KPI adopted org-wide."]


def test_check_flags_invented_facts(base):
    import copy
    t = copy.deepcopy(base)
    t["experience"][0]["bullets"][0] = "Led causal ML pricing for 90,000 homes; cut time to lease 4.8% (~$8M/yr)."
    t["skills"][0]["items"] = "Scikit-learn, PyTorch"
    assert rp.check(t, base) == []
    t["experience"][0]["bullets"][1] = "Shipped a recommender serving 2M users."
    t["skills"][1]["items"] += ", Rust"
    t["experience"][1]["dates"] = "2023–2026"
    problems = "\n".join(rp.check(t, base))
    assert "2m" in problems and "Rust" in problems and "company/dates" in problems


def test_render_one_page_and_verify_roundtrip(base, tmp_path):
    pdf = tmp_path / "r.pdf"
    info = rp.render(base, pdf)
    assert info["pages"] == 1 and info["bytes"] < 12000          # small enough to attach as base64
    m = EmailMessage()
    m["To"], m["Subject"] = "x@example.com", "hi"
    m.set_content("body")
    m.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename="Jane_Doe_Resume.pdf")
    raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
    assert rp.verify('{"id": "r1", "raw": "%s"}' % raw, pdf)[0]
    bad = bytearray(pdf.read_bytes())
    bad[500] ^= 1
    m.get_payload()[1].set_content(bytes(bad), maintype="application", subtype="pdf", filename="Jane_Doe_Resume.pdf")
    assert not rp.verify(m.as_string(), pdf)[0]


def test_long_resume_shrinks_to_one_page(base, tmp_path):
    base["experience"][0]["bullets"] *= 18
    info = rp.render(base, tmp_path / "long.pdf")
    assert info["pages"] == 1 and info["font_size"] < 10.5
    base["experience"][0]["bullets"] *= 3                       # too long at any size: reported, not hidden
    assert rp.render(base, tmp_path / "long.pdf")["pages"] > 1


def test_cli_render_refuses_untruthful(base, tmp_path):
    import json
    (tmp_path / "base.json").write_text(json.dumps(base))
    base["experience"][0]["bullets"][0] += " Grew revenue 40%."
    (tmp_path / "t.json").write_text(json.dumps(base))
    out = str(tmp_path / "t.pdf")
    assert main(["resume-pdf", "render", str(tmp_path / "t.json"), "-o", out, "--base", str(tmp_path / "base.json")]) == 2
    assert main(["resume-pdf", "render", str(tmp_path / "base.json"), "-o", out]) == 0
    assert (tmp_path / "t.pdf.b64").exists()
