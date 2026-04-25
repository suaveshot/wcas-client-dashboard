"""
Post-activation sample-output generator.

After `mark_activation_complete` fires, the dashboard runs one Opus call
per pipeline to produce a real sample output grounded in the tenant's KB.
This is what judges see at the end of the onboarding demo - tangible
proof the automations can produce content in the client's voice from
data the orchestrator captured during the conversation.

Seven templates, one per pipeline in the post-refactor roster:

    gbp -> first-month GBP post draft
    seo -> one-paragraph SEO health summary + 2-3 wins
    reviews -> review-reply draft for the most recent review
    email_assistant -> reply to a representative inbound inquiry
    chat_widget -> sample chat turn where a visitor asks a core question
    blog -> 400-word blog post in the client's voice
    social -> 3-post week (caption + image description) for FB/IG

All seven calls share a cached tenant-context block (every KB section
concatenated) so the KB goes through Anthropic's prompt cache once per
batch and subsequent per-pipeline prompts stay cheap.

Samples persist to `/opt/wc-solns/<tenant>/samples/<slug>.json` so the UI
can re-render them without regenerating. Regenerating is a single
explicit call (overwrites the file), not automatic.

Cost safety:
  - Each call goes through opus.chat which calls cost_tracker.should_allow
  - If the per-tenant cap is reached mid-batch, remaining samples record
    a "budget_exceeded" placeholder instead of a real output - partial
    success is better than a full failure on the demo.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store, opus, tenant_kb

log = logging.getLogger("dashboard.sample_outputs")


# ---------------------------------------------------------------------------
# Per-pipeline prompt templates
# ---------------------------------------------------------------------------

# Output shape every template agrees on: a single JSON object with
# {"title": str, "body_markdown": str, "preview": str}. The agent returns
# ONLY that JSON, no prose, no fences. We parse + fall back gracefully.

_OUTPUT_CONTRACT = """Respond with ONLY a JSON object matching this shape (no prose, no fences):
{
  "title": "short heading to display on the card (max 80 chars)",
  "body_markdown": "the sample content, in markdown (headings, bullets, paragraphs allowed)",
  "preview": "one short sentence summarizing what this sample is (max 160 chars)"
}

Hard rules:
- No em dashes. Use commas, periods, or parentheses.
- No emoji.
- Never mention any AI vendor by name (no Claude, Opus, Anthropic, GPT).
- Use the client's voice + services + policies verbatim from the KB context above.
- Be specific to this business. Names of classes, services, hours, tone all come from the KB.
- If the KB lacks data you need, invent nothing that would be business-sensitive (prices, dates, legal claims). Keep the sample realistic but obviously a draft."""


# Each template advertises its citations: which KB sections feed its voice,
# which CRM fields/segments feed its data, which WCAS playbook drives its
# mechanics. The UI renders these as small provenance badges under each
# sample so judges (and owners) see where every word came from. Citations
# are deterministic per template; no Opus call required to compute them.
_CITATIONS: dict[str, list[dict[str, str]]] = {
    "gbp": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "services"},
        {"kind": "playbook", "source": "gbp_post"},
    ],
    "seo": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "company"},
        {"kind": "playbook", "source": "seo_health"},
    ],
    "reviews": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "services"},
        {"kind": "playbook", "source": "review_reply"},
    ],
    "email_assistant": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "services"},
        {"kind": "playbook", "source": "inbound_reply"},
    ],
    "chat_widget": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "faq"},
        {"kind": "playbook", "source": "chat_turn"},
    ],
    "blog": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "services"},
        {"kind": "playbook", "source": "blog_post"},
    ],
    "social": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "services"},
        {"kind": "playbook", "source": "social_week"},
    ],
    "live_simulation": [
        {"kind": "voice", "source": "voice"},
        {"kind": "data", "source": "last_engagement"},
        {"kind": "playbook", "source": "re_engagement"},
    ],
}


_TEMPLATES: dict[str, str] = {
    "gbp": (
        "Draft this business's first Google Business Profile post of the month. "
        "One short post (under 100 words) that's on-brand + promotes an actual service "
        "they offer + has a clear call-to-action. The title should be the post's main "
        "headline; the body_markdown is the GBP post itself. " + _OUTPUT_CONTRACT
    ),
    "seo": (
        "Write a one-paragraph SEO + Google Business health summary for this client. "
        "Speak to them directly. Mention 2-3 specific opportunities based on what you "
        "know about their services, location, and voice from the KB. The body_markdown "
        "should feel like a brief from a senior SEO consultant, not a listicle. " + _OUTPUT_CONTRACT
    ),
    "reviews": (
        "Draft a review-reply this business could send for a recent 5-star review. "
        "Invent a realistic-sounding review that fits their actual services, then write "
        "the reply in the client's voice from the KB. Thank the reviewer, mention a "
        "specific service they offer, leave the door open to another visit. Keep the "
        "reply under 80 words. The title is 'Reply to: <first 6 words of the review>...'. "
        "body_markdown starts with > (blockquote) for the review, then the reply text. " + _OUTPUT_CONTRACT
    ),
    "email_assistant": (
        "A potential customer has just emailed asking a common inbound question for "
        "this business (pick one that fits their actual services: pricing, availability, "
        "a specific service detail, etc). Draft the reply the email assistant would stage "
        "for the owner's approval. Sound like the owner, per the KB voice section. "
        "Title is 'Draft reply: <their question in 6 words>'. body_markdown includes the "
        "incoming question as a blockquote, then the drafted reply. Keep reply under 150 "
        "words. Close with a specific next step (book a call, visit the site, reply here). "
        + _OUTPUT_CONTRACT
    ),
    "chat_widget": (
        "Show what the site chat widget would say if a visitor typed: 'How do I get "
        "started?' (or the equivalent question for this specific business - a dance studio "
        "visitor might ask about class schedules, an HVAC caller about dispatch times). "
        "Render it as a short chat exchange: the visitor's message, then the widget's "
        "reply. The widget should sound like the owner, not corporate. Under 80 words total. "
        "title: 'Live chat sample'. body_markdown uses **Visitor:** and **WCAS:** labels. "
        + _OUTPUT_CONTRACT
    ),
    "blog": (
        "Write a full ~400-word blog post this business could publish this month. Pick "
        "a topic relevant to their actual services (not generic - something only this "
        "business would write about). Structure: an opening hook, 2-3 body sections with "
        "subheadings, a short closing that invites the reader to get in touch. Use "
        "markdown headings (##) for subheadings. Voice matches the KB. Title is the blog "
        "post's actual title. " + _OUTPUT_CONTRACT
    ),
    "social": (
        "Plan a week of Facebook + Instagram posts for this business. Three posts total, "
        "spread across the week. Each post has a caption (under 220 chars) + a one-line "
        "image description the owner (or their assistant) can shoot in 10 minutes. "
        "Match the client's voice from the KB. Posts should be varied: one educational, "
        "one behind-the-scenes, one promotional. title: 'Social week draft: 3 posts'. "
        "body_markdown uses ### for each post's day (Monday, Wednesday, Friday) + bullet "
        "points for caption and image-direction. " + _OUTPUT_CONTRACT
    ),
    # Live customer simulation: takes a SPECIFIC named customer + how many
    # days they've been inactive and drafts the actual re-engagement email
    # the email_assistant pipeline would queue for owner approval. This is
    # the demo finale: judges see the entire chain (voice extraction + CRM
    # mapping + voice-matched drafting) collapse into one personalized
    # message addressed to a real-looking person.
    #
    # The endpoint substitutes {name} and {days_inactive} into the template
    # before sending; KB context provides the voice + service vocabulary.
    "live_simulation": (
        "A specific customer of this business hasn't engaged in a while: "
        "name={name}, days since last engagement={days_inactive}. "
        "Draft the personalized re-engagement email the email_assistant "
        "pipeline would queue for the owner's approval. Address them by "
        "first name. Reference the kind of service they would have last "
        "used (pull from SERVICES section). Voice MUST match the VOICE "
        "section verbatim (warm, in-the-owner's-words, not corporate). "
        "Keep the email under 130 words. End with a specific, low-friction "
        "next step (book a class, reply to this email, drop in this week). "
        "title: 'Re-engagement draft for ' + the first name. "
        "body_markdown is the email itself: Subject line first, blank line, "
        "then the body. " + _OUTPUT_CONTRACT
    ),
}


# ---------------------------------------------------------------------------
# Context assembly + file persistence
# ---------------------------------------------------------------------------


def _assemble_tenant_context(tenant_id: str) -> str:
    """Concatenate every KB section the sample generator needs, with labels.
    Returned as a single string that goes into the opus.chat system block
    with cache_system=True."""
    sections: list[tuple[str, str | None]] = [
        ("COMPANY", tenant_kb.read_section(tenant_id, "company")),
        ("SERVICES", tenant_kb.read_section(tenant_id, "services")),
        ("VOICE / TONE", tenant_kb.read_section(tenant_id, "voice")),
        ("POLICIES", tenant_kb.read_section(tenant_id, "policies")),
        ("PRICING", tenant_kb.read_section(tenant_id, "pricing")),
        ("FAQ", tenant_kb.read_section(tenant_id, "faq")),
        ("KNOWN CONTACTS", tenant_kb.read_section(tenant_id, "known_contacts")),
        ("EXISTING STACK", tenant_kb.read_section(tenant_id, "existing_stack")),
    ]
    blocks: list[str] = [
        "You are generating a first-week sample output for a real small business.",
        "Everything you know about the business is in the KB below. Use it verbatim.",
        "",
    ]
    for label, body in sections:
        if body and body.strip():
            blocks.append(f"## {label}\n{body.strip()}\n")
    blocks.append(
        "Stay in the client's voice. Everything you produce should sound like it "
        "came from the owner, not a marketing agency."
    )
    return "\n".join(blocks)


def _samples_dir(tenant_id: str) -> Path:
    root = heartbeat_store.tenant_root(tenant_id) / "samples"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_sample(tenant_id: str, slug: str) -> dict[str, Any] | None:
    path = _samples_dir(tenant_id) / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_sample(tenant_id: str, slug: str, payload: dict[str, Any]) -> Path:
    path = _samples_dir(tenant_id) / f"{slug}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except (PermissionError, NotImplementedError, OSError):
        pass
    return path


def list_samples(tenant_id: str) -> list[dict[str, Any]]:
    """Return every cached sample for this tenant, sorted by pipeline slug order.
    Missing samples are omitted; caller can fill gaps via generate_for_pipeline."""
    out: list[dict[str, Any]] = []
    ordered = ("gbp", "seo", "reviews", "email_assistant", "chat_widget", "blog", "social")
    for slug in ordered:
        s = _load_sample(tenant_id, slug)
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Per-pipeline generator
# ---------------------------------------------------------------------------


def _parse_output(raw_text: str, slug: str) -> dict[str, Any]:
    """Parse the model's JSON response. Fall back to a simple wrapper if
    it didn't produce valid JSON so the demo never goes blank."""
    text = (raw_text or "").strip()
    # Strip optional code fences.
    if text.startswith("```"):
        end = text.rfind("```")
        if end > 3:
            inner = text[3:end]
            if inner.lower().startswith(("json\n", "json\r", "json ")):
                inner = inner.split("\n", 1)[1] if "\n" in inner else ""
            text = inner.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {
            "title": f"{slug} sample",
            "body_markdown": raw_text or "_no content returned_",
            "preview": "Fallback wrapper: model output was not valid JSON.",
        }
    if not isinstance(parsed, dict):
        return {
            "title": f"{slug} sample",
            "body_markdown": str(parsed),
            "preview": "Fallback wrapper: expected JSON object.",
        }
    title = str(parsed.get("title") or f"{slug} sample").strip()[:120]
    body = str(parsed.get("body_markdown") or "").strip() or "_empty body_"
    preview = str(parsed.get("preview") or "").strip()[:200]
    return {"title": title, "body_markdown": body, "preview": preview}


def citations_for(slug: str) -> list[dict[str, str]]:
    """Return the canonical provenance badges for a sample slug. Empty list
    for unknown slugs so downstream code never crashes on a typo."""
    return list(_CITATIONS.get(slug, []))


def generate_for_pipeline(
    tenant_id: str,
    slug: str,
    *,
    context: str | None = None,
    model: str | None = None,
    template_vars: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Generate + persist one sample output for a single pipeline.

    `context` lets callers pass a pre-assembled tenant context so a batch
    of 7 calls shares one cached system block (see generate_all_for_tenant).
    `template_vars` lets parameterized templates (e.g. live_simulation)
    substitute {name}, {days_inactive}, etc. into the prompt body.
    `persist=False` suppresses the on-disk write (used by the simulate
    endpoint, which returns the draft directly without polluting the
    samples/ directory).
    """
    if slug not in _TEMPLATES:
        raise ValueError(f"unknown pipeline slug: {slug!r}")

    context_block = context or _assemble_tenant_context(tenant_id)
    prompt = _TEMPLATES[slug]
    if template_vars:
        try:
            prompt = prompt.format(**template_vars)
        except (KeyError, IndexError) as exc:
            raise ValueError(
                f"template_vars missing required key for slug {slug!r}: {exc}"
            ) from exc

    try:
        result = opus.chat(
            tenant_id=tenant_id,
            system=context_block,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1400,
            temperature=0.6,
            kind="sample_output",
            note=f"sample:{slug}",
            cache_system=True,
            model=model,
        )
    except opus.OpusBudgetExceeded as exc:
        payload = {
            "slug": slug,
            "kind": "sample_output",
            "status": "budget_exceeded",
            "title": f"{slug.replace('_', ' ').title()} sample",
            "body_markdown": (
                f"_Daily Opus budget reached while generating this sample. "
                f"Rerun after the cap resets, or lift DAILY_TENANT_CAP. Reason: {exc}_"
            ),
            "preview": "Budget exceeded during generation.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "citations": citations_for(slug),
        }
        if persist:
            _save_sample(tenant_id, slug, payload)
        return payload
    except opus.OpusUnavailable as exc:
        payload = {
            "slug": slug,
            "kind": "sample_output",
            "status": "unavailable",
            "title": f"{slug.replace('_', ' ').title()} sample",
            "body_markdown": f"_Sample generator unavailable: {exc}_",
            "preview": "Opus unavailable.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "citations": citations_for(slug),
        }
        if persist:
            _save_sample(tenant_id, slug, payload)
        return payload

    parsed = _parse_output(result.text, slug)
    payload = {
        "slug": slug,
        "kind": "sample_output",
        "status": "ok",
        "title": parsed["title"],
        "body_markdown": parsed["body_markdown"],
        "preview": parsed["preview"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "usd": result.usd,
        "citations": citations_for(slug),
    }
    if persist:
        _save_sample(tenant_id, slug, payload)
    return payload


def generate_all_for_tenant(tenant_id: str, *, model: str | None = None) -> list[dict[str, Any]]:
    """Batch generate all 7 user-facing samples. Shares one cached system
    block across calls. Skips live_simulation - that one is generated
    on-demand by the simulate-customer endpoint with template_vars."""
    context = _assemble_tenant_context(tenant_id)
    out: list[dict[str, Any]] = []
    for slug in _TEMPLATES.keys():
        if slug == "live_simulation":
            continue
        try:
            out.append(generate_for_pipeline(tenant_id, slug, context=context, model=model))
        except Exception: # defensive: one failure doesn't kill the batch
            log.exception("sample generation failed slug=%s tenant=%s", slug, tenant_id)
            fallback = {
                "slug": slug,
                "kind": "sample_output",
                "status": "error",
                "title": f"{slug.replace('_', ' ').title()} sample",
                "body_markdown": "_Generation failed. Retry once the issue is resolved._",
                "preview": "Error during generation.",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "usd": 0.0,
                "citations": citations_for(slug),
            }
            _save_sample(tenant_id, slug, fallback)
            out.append(fallback)
    return out


# Pipeline slugs that ship as part of the post-activation samples grid
# (live_simulation is generated on-demand, not batched).
PIPELINE_SLUGS: tuple[str, ...] = tuple(s for s in _TEMPLATES.keys() if s != "live_simulation")
