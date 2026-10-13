"""Fill and submit job application forms (Greenhouse, Lever, Ashby) in headless Chromium.

Fields are found by their visible labels, so one engine handles all three ATSs. This module never
solves or evades CAPTCHAs: if a challenge appears, the application is reported as blocked and left
for you to submit by hand.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

SUCCESS = re.compile(r"thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
                     r"(submitted|received)|we('ve| have) received your application|successfully (submitted|applied)",
                     re.I)

_SCAN_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && (r.width > 0 || r.height > 0 || el.type === 'file'); };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(l => l.innerText).join(' '));
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = clean(lb.split(' ').map(i => document.getElementById(i)?.innerText || '').join(' ')); if (t) return t; }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    return '';
  };
  const groupLabel = (els, optionTexts) => {
    let a = els[0].parentElement;
    while (a && !els.every(x => a.contains(x))) a = a.parentElement;
    for (let k = 0; k < 6 && a; k++, a = a.parentElement) {
      const lg = a.querySelector('legend');
      if (lg && clean(lg.innerText)) return clean(lg.innerText);
      const lines = (a.innerText || '').split('\n').map(clean).filter(t => t && t !== '*' && !optionTexts.includes(t));
      if (lines.length) return lines[0];
    }
    return '';
  };
  const nearLabel = el => {            // question text in the field's own container (Lever cards, Ashby)
    let a = el.parentElement;
    for (let k = 0; k < 5 && a; k++, a = a.parentElement) {
      if (a.querySelectorAll('input:not([type=hidden]), textarea, select').length > 1) break;
      const own = el.options ? [...el.options].map(o => clean(o.text)) : [];
      const lines = (a.innerText || '').split('\n').map(clean).filter(t => t && t !== '*' && t !== el.value && !own.includes(t));
      if (lines.length) return lines[0];
    }
    return '';
  };
  const out = [], groups = {};
  let q = 0;
  // Yes/No button pairs (Ashby booleans): tag the container so fill() can find it again.
  for (const yes of document.querySelectorAll('button')) {
    if (clean(yes.innerText) !== 'Yes') continue;
    const box = yes.parentElement, no = [...box.querySelectorAll('button')].find(b => clean(b.innerText) === 'No');
    if (!no || !vis(yes)) continue;
    let a = box.parentElement, label = '';
    for (let k = 0; k < 4 && a && !label; k++, a = a.parentElement) {
      label = (a.innerText || '').split('\n').map(clean).filter(t => t && !['Yes', 'No', '*'].includes(t))[0] || '';
    }
    box.setAttribute('data-sourcer-q', String(++q));
    out.push({key: `[data-sourcer-q="${q}"]`, label, kind: 'buttons', required: /\*/.test(label), options: ['Yes', 'No']});
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(type)) continue;
    if ((el.name || '').startsWith('g-recaptcha') || (el.name || '').startsWith('h-captcha')) continue;
    if (el.closest('[aria-hidden=true]') || el.tabIndex === -1 && type !== 'file') continue;
    if (!vis(el)) continue;
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (type === 'radio' || type === 'checkbox') {
      const key = el.name || el.id; if (!key) continue;
      (groups[key] = groups[key] || {els: [], required: false}).els.push(el);
      groups[key].required ||= required;
      continue;
    }
    let sel = el.id ? `[id="${el.id}"]` : (el.name ? `[name="${el.name}"]` : '');
    if (!sel) { el.setAttribute('data-sourcer-q', String(++q)); sel = `[data-sourcer-q="${q}"]`; }
    let label = labelOf(el) || nearLabel(el), kind = type === 'select-one' ? 'select' : (el.tagName === 'TEXTAREA' ? 'textarea' : type);
    if (el.getAttribute('role') === 'combobox' && el.tagName === 'INPUT') kind = 'combobox';
    if (type === 'file') {
      const id = (el.id + ' ' + el.name).toLowerCase();
      const near = clean(el.closest('div[class*=field], div[class*=upload], fieldset, li, .application-field')?.innerText || '');
      label = /cover/.test(id) ? 'Cover Letter' : (/resume|cv/.test(id) ? 'Resume/CV' : (near.split(' Attach')[0] || label));
    }
    const options = kind === 'select' ? [...el.options].filter(o => o.value !== '').map(o => clean(o.text)).filter(t => t && !/^(select|choose|--)/i.test(t)) : [];
    out.push({key: sel, label, kind, required, options});
  }
  for (const [key, g] of Object.entries(groups)) {
    const opts = g.els.map(x => labelOf(x) || clean(x.value));
    out.push({key: `[name="${key}"]`, label: groupLabel(g.els, opts), kind: g.els[0].type, required: g.required, options: opts});
  }
  return out;
}
"""


@dataclass
class Field:
    key: str
    label: str
    kind: str
    required: bool = False
    options: list[str] = field(default_factory=list)

    @property
    def norm(self) -> str:
        return norm(self.label)


def norm(label: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\(required\)|\*", "", label.lower())).split("  ")[0].strip()[:120]


def _proxy_args() -> dict:
    """Route Chromium through the environment's HTTPS proxy and trust that proxy's CA (by key pin),
    when one is configured. Certificate checks stay on for everything else."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    kw: dict = {"args": []}
    if proxy:
        kw["proxy"] = {"server": proxy}
    ca = os.environ.get("SOURCER_PROXY_CA", "/root/.ccr/agent-proxy-ca.crt")
    if proxy and Path(ca).exists():
        try:
            pub = subprocess.run(["openssl", "x509", "-in", ca, "-pubkey", "-noout"], capture_output=True, check=True).stdout
            der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=pub, capture_output=True,
                                 check=True).stdout
            import base64
            import hashlib
            kw["args"].append("--ignore-certificate-errors-spki-list=" + base64.b64encode(hashlib.sha256(der).digest()).decode())
        except (OSError, subprocess.CalledProcessError):
            pass
    exe = os.environ.get("SOURCER_CHROMIUM") or next(
        (p for p in ("/opt/pw-browsers/chromium",) if Path(p).exists()), None)
    if exe:
        kw["executable_path"] = exe
    return kw


class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(**_proxy_args())

    def page(self):
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-US",
                                       timezone_id="America/Los_Angeles")
        return ctx.new_page()

    def close(self):
        self.browser.close()
        self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_form(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001 - some boards keep a socket open
        pass
    try:                                    # React forms (Ashby) render after load
        page.wait_for_selector("input[type=file], input[type=email], textarea", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    # Greenhouse/Lever pages sometimes show the description first, with an Apply button.
    for name in (r"^apply( for this job| now)?$", r"^apply"):
        btn = page.get_by_role("button", name=re.compile(name, re.I))
        if btn.count() and not page.locator("input[type=file]").count():
            try:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001
                pass
            break


def _menu(page, el):
    """The listbox a combobox controls (not every [role=option] on the page - phone pickers have those too)."""
    for attr in ("aria-controls", "aria-owns"):
        mid = el.get_attribute(attr)
        if mid:
            return page.locator(f'[id="{mid}"] [role=option]')
    return page.locator("[class*=menu] [role=option]")


def _combobox_options(page, key: str) -> list[str]:
    try:
        el = page.locator(key).first
        el.click(timeout=3000)
        page.wait_for_timeout(500)
        opts = [t.strip() for t in _menu(page, el).all_inner_texts() if t.strip()]
        page.keyboard.press("Escape")
        return opts
    except Exception:  # noqa: BLE001
        return []


def scan(page) -> list[Field]:
    fields = [Field(**f) for f in page.evaluate(_SCAN_JS)]
    for f in fields:
        if f.kind == "combobox":
            f.options = _combobox_options(page, f.key)
            if not f.options:
                f.kind = "autocomplete"     # e.g. Greenhouse's location lookup
    seen, out = set(), []
    for f in fields:
        if f.label and (f.key, f.label) not in seen:
            seen.add((f.key, f.label))
            out.append(f)
    return out


def attach_resume(page, resume: Path) -> bool:
    """Upload the resume before reading the form: some ATSs (Ashby) parse it and redraw the form."""
    for sel in ("input[type=file][id*=resume i]", "input[type=file][name*=resume i]", "input[type=file]"):
        loc = page.locator(sel)
        if loc.count():
            loc.first.set_input_files(str(resume))
            page.wait_for_timeout(4000)
            return True
    return False


def _choose(options: list[str], value: str) -> str | None:
    v = value.strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    hits = [o for o in options if v and (v in o.lower() or o.lower() in v)]
    return hits[0] if len(hits) == 1 else None


ATTACHED = Path("<already attached>")


def fill(page, fields: list[Field], answers: dict[str, str], resume: Path | None) -> list[str]:
    """Fill fields from {normalized label: answer}. Returns problems (required fields left empty,
    options that didn't match)."""
    problems = []
    for f in fields:
        val = answers.get(f.norm)
        if f.kind == "file":
            if resume is ATTACHED:
                continue
            if resume and re.search(r"resume|cv", f.label, re.I):
                page.locator(f.key).first.set_input_files(str(resume))
            elif f.required:
                problems.append(f"required upload: {f.label}")
            continue
        if val is None or str(val).strip() == "":
            if f.required:
                problems.append(f"no answer for required question: {f.label}")
            continue
        val = str(val)
        try:
            loc = page.locator(f.key)
            if f.kind in ("text", "textarea", "email", "tel", "url", "number"):
                loc.first.fill(val)
            elif f.kind == "select":
                opt = _choose(f.options, val)
                if not opt:
                    problems.append(f"'{val}' isn't an option for: {f.label}")
                    continue
                loc.first.select_option(label=opt)
            elif f.kind == "combobox":
                opt = _choose(f.options, val)
                if not opt:
                    problems.append(f"'{val}' isn't an option for: {f.label}")
                    continue
                loc.first.click()
                page.wait_for_timeout(300)
                _menu(page, loc.first).filter(has_text=re.compile(rf"^\s*{re.escape(opt)}\s*$")).first.click()
            elif f.kind == "autocomplete":
                loc.first.scroll_into_view_if_needed()
                loc.first.click(force=True)
                loc.first.press_sequentially(val.split(",")[0], delay=40)
                page.wait_for_timeout(1500)
                options = _menu(page, loc.first)
                if options.count():
                    options.first.click()
                else:
                    loc.first.fill(val)
            elif f.kind == "buttons":
                opt = _choose(f.options, val)
                if not opt:
                    problems.append(f"'{val}' isn't an option for: {f.label}")
                    continue
                loc.first.get_by_role("button", name=opt, exact=True).click()
            elif f.kind in ("radio", "checkbox"):
                wanted = [v.strip() for v in (val.split(";") if f.kind == "checkbox" else [val])]
                for w in wanted:
                    opt = _choose(f.options, w)
                    if not opt:
                        problems.append(f"'{w}' isn't an option for: {f.label}")
                        continue
                    idx = f.options.index(opt)
                    loc.nth(idx).check(force=True)
            page.wait_for_timeout(150)
        except Exception as e:  # noqa: BLE001
            problems.append(f"couldn't fill {f.label}: {type(e).__name__}")
    return problems


def _captcha_challenge(page) -> bool:
    for fr in page.frames:
        u = fr.url or ""
        if "recaptcha/api2/bframe" in u or "recaptcha/enterprise/bframe" in u or ("hcaptcha" in u and "challenge" in u):
            try:
                el = fr.frame_element()
                box = el.bounding_box()
                if box and box["height"] > 100 and el.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def submit(page, wait_s: int = 30) -> tuple[str, str]:
    """Click submit and classify the outcome: submitted | blocked (CAPTCHA) | failed."""
    btn = page.get_by_role("button", name=re.compile(r"submit", re.I))
    if not btn.count():
        btn = page.locator("button[type=submit], input[type=submit]")
    if not btn.count():
        return "failed", "no submit button found"
    start_url = page.url
    btn.last.click()
    deadline = time.time() + wait_s
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        body = page.inner_text("body")[:20000] if page.locator("body").count() else ""
        if SUCCESS.search(body) or re.search(r"confirmation|thank", page.url, re.I) and page.url != start_url:
            return "submitted", "confirmation page shown"
        if _captcha_challenge(page):
            return "blocked", "CAPTCHA challenge - submit this one yourself"
    errs = page.locator("[aria-invalid=true], .error, .field-error, [class*=error]").all_inner_texts()
    errs = [e.strip() for e in errs if e.strip()][:5]
    return "failed", ("form errors: " + " | ".join(errs)) if errs else "no confirmation after submitting"
