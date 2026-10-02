#!/usr/bin/env python3
"""sre-agent v1 (backlog #66, ADR 0039): an offline diagnosis harness.

Sends a real incident bundle (context + symptom only, never the
reference_answer) to Claude via the Anthropic Messages API, asking for
a structured triage response. Does not grade its own output -- ADR
0039's own design is a human comparing the response against each
bundle's real reference_answer (agreement / partial / miss /
hallucinated), the same honest self-grading bar backlog #102/#108
already applied to themselves. Auto-grading would just move the
question of "is this diagnosis right" onto a second, unverified model
call instead of answering it.

No cluster access, no write/remediation action of any kind -- reads
one local bundle file, makes one API call, prints the result. A
co-pilot's static test harness, not an operator.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

SYSTEM_PROMPT = """You are an SRE incident-triage assistant. You will be given a real \
incident bundle from a live Kubernetes-based platform-engineering project: some \
deployment context and an observed symptom (logs, error messages, query results). \
You are NOT given the real root cause or fix -- your job is to diagnose it.

Respond with a single JSON object, no other text, with exactly these fields:
- "summary": one or two sentences describing what's actually happening, in your own words.
- "suspected_root_cause": your best-supported hypothesis for the real underlying cause.
- "affected_components": a list of the real component names involved.
- "suggested_next_steps": a list of concrete, specific actions a human could take next \
  (not remediation you perform yourself -- you have no access to the cluster).
- "confidence": a number from 0 to 1, your own honest self-assessed confidence in the \
  suspected_root_cause above.

Ground every claim in the bundle's own real content. If the bundle doesn't contain enough \
information to be confident, say so plainly in suggested_next_steps rather than guessing \
past what the evidence actually supports."""


def load_bundle(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def build_user_message(bundle: dict) -> str:
    # Deliberately excludes bundle["reference_answer"] -- the harness's
    # entire point is testing diagnosis from context+symptom alone.
    payload = {
        "incident_id": bundle["incident_id"],
        "component": bundle["component"],
        "context": bundle["context"],
        "symptom": bundle["symptom"],
        "note_to_agent": bundle.get("note_to_agent", ""),
    }
    return json.dumps(payload, indent=2)


def run(bundle_path: Path, model: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY is not set. This harness makes a real API call and "
            "needs a real key -- see sre-agent/README.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    bundle = load_bundle(bundle_path)
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(bundle)}],
    )

    raw_text = "".join(block.text for block in message.content if block.type == "text")
    try:
        diagnosis = json.loads(raw_text)
    except json.JSONDecodeError:
        diagnosis = {"_parse_error": True, "_raw_text": raw_text}

    return {
        "incident_id": bundle["incident_id"],
        "model": model,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
        "diagnosis": diagnosis,
        "reference_answer": bundle["reference_answer"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bundle",
        type=Path,
        nargs="?",
        help="Path to a single bundle JSON file. Omit to run all bundles in bundles/.",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-5",
        help="Anthropic model id to grade (default: claude-sonnet-5).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory to write result JSON files to (default: print to stdout only).",
    )
    args = parser.parse_args()

    if args.bundle:
        bundle_paths = [args.bundle]
    else:
        bundle_paths = sorted((Path(__file__).parent / "bundles").glob("*.json"))

    for bp in bundle_paths:
        print(f"--- {bp.name} ---", file=sys.stderr)
        result = run(bp, args.model)
        text = json.dumps(result, indent=2)
        print(text)
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            out_path = args.out / f"{result['incident_id']}.result.json"
            out_path.write_text(text)
            print(f"wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
