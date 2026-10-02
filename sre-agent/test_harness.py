#!/usr/bin/env python3
"""Real, structural test: reference_answer must never leak into the
prompt sent to the model -- the harness's entire integrity guarantee.
No network call, no API key needed. Also proves every real bundle file
is well-formed with the required fields, before it's ever spent
against a real API call."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harness import build_user_message, load_bundle  # noqa: E402

BUNDLES_DIR = Path(__file__).parent / "bundles"
REQUIRED_FIELDS = {"incident_id", "component", "context", "symptom", "reference_answer"}


def test_no_answer_leakage():
    for bp in sorted(BUNDLES_DIR.glob("*.json")):
        bundle = load_bundle(bp)
        user_message = build_user_message(bundle)
        parsed = json.loads(user_message)
        assert "reference_answer" not in parsed, f"{bp.name}: reference_answer leaked into prompt payload"
        # Also check the real, distinctive answer text itself doesn't appear
        # anywhere in the serialized message, not just the top-level key.
        answer_text = json.dumps(bundle["reference_answer"])
        # A crude but real substring check on the actual root_cause sentence.
        root_cause = bundle["reference_answer"]["root_cause"]
        assert root_cause not in user_message, f"{bp.name}: real root_cause text leaked verbatim into prompt"
        print(f"OK  {bp.name}: no answer leakage")


def test_bundle_shape():
    bundles = sorted(BUNDLES_DIR.glob("*.json"))
    assert bundles, "no bundle files found"
    for bp in bundles:
        bundle = load_bundle(bp)
        missing = REQUIRED_FIELDS - bundle.keys()
        assert not missing, f"{bp.name}: missing required fields {missing}"
        assert "root_cause" in bundle["reference_answer"], f"{bp.name}: reference_answer missing root_cause"
        assert "fix_applied" in bundle["reference_answer"], f"{bp.name}: reference_answer missing fix_applied"
        print(f"OK  {bp.name}: required fields present")


if __name__ == "__main__":
    test_bundle_shape()
    test_no_answer_leakage()
    print(f"\n{len(list(BUNDLES_DIR.glob('*.json')))} bundles, all checks passed.")
