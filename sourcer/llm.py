"""Optional Claude pass: re-rank the top postings and draft outreach notes.

Enabled when ANTHROPIC_API_KEY is set (disable with SOURCER_LLM=0). Only the
highest-scoring candidates are sent, so a run costs cents.
"""
from __future__ import annotations

import json
import logging
import os

from .models import OutreachLead, ScoredJob

log = logging.getLogger(__name__)

MODEL = os.environ.get("SOURCER_MODEL", "claude-opus-5-5")

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "integer", "description": "0-100 fit of candidate to role"},
        "why": {"type": "string", "description": "One sentence: strongest reason it fits."},
        "gap": {"type": "string", "description": "One short phrase: biggest gap, or empty."},
    },
    "required": ["fit", "why", "gap"],
    "additionalProperties": False,
}

MSG_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("SOURCER_LLM", "1") != "0"


def _client():
    import anthropic
    return anthropic.Anthropic()


def _ask(client, prompt: str, schema: dict, max_tokens: int = 2000) -> dict | None:
    import anthropic
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError as e:
        log.warning("Claude rate limited: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Claude unreachable: %s", e)
        return None
    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude stopped with %s", resp.stop_reason)
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def rerank(jobs: list[ScoredJob], profile: dict, top_n: int = 15) -> None:
    """Blend Claude's fit rating into the top `top_n` scores, in place."""
    if not enabled() or not jobs:
        return
    client = _client()
    for sj in sorted(jobs, key=lambda x: -x.score)[:top_n]:
        j = sj.job
        prompt = (
            "You screen job postings for one candidate. Rate how well the candidate fits this "
            "role and how likely they are to get an interview.\n\n"
            f"<candidate>\n{profile.get('summary', '').strip()}\n</candidate>\n\n"
            f"<posting>\nCompany: {j.company}\nTitle: {j.title}\nLocation: {j.location}\n\n"
            f"{j.description[:15000]}\n</posting>"
        )
        out = _ask(client, prompt, FIT_SCHEMA, max_tokens=1500)
        if not out:
            continue
        fit = max(0, min(100, int(out.get("fit", 0))))
        sj.score = round(0.5 * sj.score + 0.5 * fit, 1)
        sj.llm_note = out.get("why", "") + (f" Gap: {out['gap']}" if out.get("gap") else "")


def draft_outreach(leads: list[OutreachLead], profile: dict, top_n: int = 5) -> None:
    if not enabled() or not leads:
        return
    client = _client()
    for lead in leads[:top_n]:
        who = lead.connections[0] if lead.connections else None
        to = (f"{who.name} ({who.position} at {lead.company}), a 1st-degree connection"
              if who else f"a data/ML leader or founder at {lead.company} (no existing connection)")
        context = lead.funding.headline if lead.funding else ""
        prompt = (
            "Write a short LinkedIn message (under 110 words, no subject line, no placeholders "
            "other than the recipient's first name if known) from the candidate below. Goal: get "
            "a 15-minute chat about data science / ML work at the company before a role is posted. "
            "Reference the specific news and one concrete way the candidate's experience applies. "
            "Plain, direct, not salesy.\n\n"
            f"<candidate>\n{profile.get('summary', '').strip()}\n</candidate>\n"
            f"<recipient>{to}</recipient>\n<news>{context}</news>\n"
            f"<why_fit>{'; '.join(lead.reasons)}</why_fit>"
        )
        out = _ask(client, prompt, MSG_SCHEMA)
        if out:
            lead.draft_message = out.get("message", "")
