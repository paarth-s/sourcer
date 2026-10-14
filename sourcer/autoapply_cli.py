"""`sourcer autoapply ...` commands. See apply.py for the flow."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from . import apply as ap


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")


def load_facts(path: str, base_dir: str = "private") -> dict:
    facts = yaml.safe_load(Path(path).read_text()) or {}
    base = None
    for v in ("ds", "mle"):
        p = Path(base_dir) / f"base_{v}.json"
        if p.exists():
            base = json.loads(p.read_text())
            break
    if base:
        c = ap.contact_parts(base["contact"], facts.get("apply_email", ""))
        facts["contact"] = {"name": base["name"], **c}
    return facts


def _select(args) -> int:
    roles = json.loads(Path(args.roles).read_text())
    exclude = set()
    if args.exclude and Path(args.exclude).exists():
        exclude = {l.strip() for l in Path(args.exclude).read_text().splitlines() if l.strip()}
    picked, skipped = ap.select(roles, exclude, limit=args.max)
    ap.save_queue(args.out, picked)
    print(f"{len(picked)} picked from {len(roles)} roles ({len(exclude)} excluded as already queued)")
    for c in picked:
        print(f"  {c.rank:5.1f}  {c.company} - {c.title} [{c.ats}, {c.variant}] {c.location}")
    reasons: dict[str, int] = {}
    for _, why in skipped:
        k = re.sub(r"\(.*\)|\d+", "", why).strip()
        reasons[k] = reasons.get(k, 0) + 1
    print("skipped: " + "; ".join(f"{n} {k}" for k, n in sorted(reasons.items(), key=lambda x: -x[1])))
    return 0


def _scan(args) -> int:
    from . import forms
    facts = load_facts(args.facts)
    cands = ap.load_queue(args.queue)
    with forms.Browser() as b:
        for c in cands:
            if c.status not in ("pending",):
                continue
            try:
                page = b.page()
                forms.open_form(page, c.apply_url)
                fields = forms.scan(page)
                page.context.close()
            except Exception as e:  # noqa: BLE001
                c.status, c.note = "manual", f"couldn't open the form ({type(e).__name__})"
                continue
            if not any(f.kind == "file" for f in fields):
                c.status, c.note = "manual", "no application form found at the apply link"
                continue
            c.answers = []
            for f in fields:
                ans, how = ap.auto_answer(f.label, f.kind, f.options, facts, c.company)
                c.answers.append({"label": f.label, "kind": f.kind, "options": f.options, "required": f.required,
                                  "answer": ans, "how": how})
            c.status = "scanned"
            todo = [a["label"] for a in c.answers if a["answer"] is None and a["kind"] != "file"]
            print(f"{c.company} - {c.title}: {len(fields)} fields, {len(todo)} for you to draft")
            for t in todo:
                print(f"    - {t[:100]}")
    ap.save_queue(args.queue, cands)
    return 0


def _doc(args) -> int:
    cands = ap.load_queue(args.queue)
    resumes = {}
    for c in cands:
        p = Path(args.resumes) / f"{_slug(c.id)}.json"
        if p.exists():
            resumes[c.id] = ap.resume_text(json.loads(p.read_text()))
        if c.status == "manual":
            continue
        missing = [a for a in c.answers if a.get("required") and a["kind"] != "file" and not (a.get("answer") or "").strip()]
        c.status = "needs_you" if missing or c.id not in resumes else "ready"
        if c.id not in resumes:
            c.note = "no tailored resume yet"
    Path(args.out).write_text(ap.render_doc(cands, resumes, args.date))
    ap.save_queue(args.queue, cands)
    n = {s: sum(c.status == s for c in cands) for s in ("ready", "needs_you", "manual")}
    print(f"{args.out}: {n['ready']} ready, {n['needs_you']} need you, {n['manual']} to apply yourself")
    return 0


def _statuses(cands, resumes_dir):
    resumes = {}
    for c in cands:
        p = Path(resumes_dir) / f"{_slug(c.id)}.json"
        if p.exists():
            resumes[c.id] = ap.resume_text(json.loads(p.read_text()))
        if c.status in ("manual", "skipped"):
            continue
        missing = [a for a in c.answers if a.get("kind") == "textarea" and a.get("required")
                   and not (a.get("answer") or "").strip()]
        c.status = "needs_you" if missing or c.id not in resumes else "ready"
        if c.id not in resumes:
            c.note = "no tailored resume yet"
    return resumes


def _doc_update(args) -> int:
    raw = json.loads(Path(args.read_doc).read_text())
    doc = raw.get("document") or (raw["content"] if isinstance(raw.get("content"), dict) else raw)
    content = (doc.get("body") or {}).get("content") or []
    if not content and doc.get("tabs"):
        content = doc["tabs"][0]["documentTab"]["body"]["content"]
    end = content[-1]["endIndex"] if content else 2
    cands = ap.load_queue(args.queue)
    resumes = _statuses(cands, args.resumes)
    reqs = ap.doc_requests(ap.doc_paragraphs(cands, resumes, args.date), end)
    Path(args.out).write_text(json.dumps({"requests": reqs, "requiredRevisionId": doc.get("revisionId", "")}, ensure_ascii=False))
    ap.save_queue(args.queue, cands)
    n = {st: sum(c.status == st for c in cands) for st in ("ready", "needs_you", "manual")}
    print(f"{args.out}: {len(reqs)} requests (replacing body up to index {end}); revisionId {doc.get('revisionId', '?')}; "
          f"{n['ready']} ready, {n['needs_you']} need you, {n['manual']} to apply yourself")
    return 0


def _kit(args) -> int:
    from .resume_pdf import render
    facts = yaml.safe_load(Path(args.facts).read_text()) or {}
    cands = ap.load_queue(args.queue)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for c in cands:
        p = Path(args.resumes) / f"{_slug(c.id)}.json"
        if not p.exists() or c.status in ("skipped", "manual"):
            continue
        r = json.loads(p.read_text())
        r["contact"] = ap.contact_parts(r["contact"], facts.get("apply_email", ""))["line"]
        info = render(r, out / ap.resume_filename(c, cands))
        if info["pages"] != 1:
            print(f"WARNING {c.company}: resume runs to {info['pages']} pages")
        made.append(info["path"])
    print("\n".join(made))
    return 0


def _submit(args) -> int:
    from . import forms
    from .resume_pdf import check, parse_text, render
    facts = load_facts(args.facts, args.base_dir)
    contact = facts["contact"]
    apps = ap.parse_doc(Path(args.doc).read_text())
    only = {int(x) for x in re.findall(r"\d+", args.only)}
    chosen = [a for a in apps if (a["n"] in only if only else a["status"].upper().startswith("READY"))]
    results = []
    out_dir = Path(args.out).parent
    with forms.Browser() as b:
        for a in chosen:
            r = {"n": a["n"], "id": a["id"], "role": a["heading"], "apply_url": a["apply_url"]}
            results.append(r)
            missing = [q["label"] for q in a["answers"] if q.get("missing")]
            if missing:
                r.update(status="needs_you", detail="unanswered: " + "; ".join(missing))
                continue
            base_p = Path(args.base_dir) / f"base_{a['variant'] or 'ds'}.json"
            base = json.loads(base_p.read_text())
            base["contact"] = contact["line"]
            tailored = parse_text(f"{base['name']}\n{contact['line']}\n{a['resume_text']}") if a["resume_text"] else base
            problems = check(tailored, base)
            if problems:
                r.update(status="failed", detail="resume check: " + "; ".join(problems))
                continue
            d = out_dir / "files" / _slug(a["id"])
            d.mkdir(parents=True, exist_ok=True)
            pdf = d / "Paarth_Sharma_Resume.pdf" if base["name"] == "Paarth Sharma" else \
                d / f"{_slug(base['name'])}_Resume.pdf"
            info = render(tailored, pdf)
            if info["pages"] != 1:
                r.update(status="failed", detail="resume runs past one page")
                continue
            answers = {}
            for q in a["answers"]:                  # same label twice (Lyft's date fields): keep the real answer
                if not q.get("from_profile") and (q["answer"] or forms.norm(q["label"]) not in answers):
                    answers[forms.norm(q["label"])] = q["answer"]
            page = b.page()
            try:
                forms.open_form(page, a["apply_url"])
                attached = forms.attach_resume(page, pdf)
                fields = forms.scan(page)
                company = a["heading"].split(" - ")[0]
                for f in fields:
                    ans, how = ap.auto_answer(f.label, f.kind, f.options, facts, company)
                    if how == "profile" and ap.IDENTITY.search(f.label) and f.kind not in ("checkbox", "radio") \
                            and ans is not None:
                        answers[f.key] = ans    # name/email/phone always from your profile, never a same-label answer
                    elif f.norm not in answers and ans is not None:
                        answers[f.norm] = ans   # a field the Doc doesn't list (form changed): standard answers only
                issues = forms.fill(page, fields, answers, forms.ATTACHED if attached else pdf)
                shot = d / "filled.png"
                page.screenshot(path=str(shot), full_page=True)
                if issues:
                    r.update(status="needs_you", detail="; ".join(issues), screenshot=str(shot))
                elif args.dry_run:
                    r.update(status="dry-run", detail=f"filled {len(fields)} fields, not submitted", screenshot=str(shot))
                else:
                    status, detail = forms.submit(page)
                    page.screenshot(path=str(d / "after-submit.png"), full_page=True)
                    r.update(status=status, detail=detail, screenshot=str(d / "after-submit.png"), resume=str(pdf))
                    time.sleep(5)
            except Exception as e:  # noqa: BLE001
                r.update(status="failed", detail=f"{type(e).__name__}: {str(e)[:200]}")
            finally:
                page.context.close()
    Path(args.out).write_text(json.dumps(results, indent=1))
    for r in results:
        print(f"{r['n']:>2}. {r['status']:<10} {r['role']} - {r.get('detail', '')}")
    return 0 if all(r["status"] in ("submitted", "dry-run") for r in results) else 1


def main(args) -> int:
    return {"select": _select, "scan": _scan, "doc": _doc, "doc-update": _doc_update,
            "kit": _kit, "submit": _submit}[args.aa_cmd](args)
