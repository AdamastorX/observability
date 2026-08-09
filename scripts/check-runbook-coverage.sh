#!/usr/bin/env bash
set -euo pipefail

# backlog #117 (staff review 2026-08-09, §7.5): every alert defined in
# platform/argocd/apps/prometheus.yaml's own alerting_rules.yml must
# have a runbook file here (backlog #22's own "one runbook per alert"
# rule), and this repo's own runbooks/README.md table must actually
# list every one of them -- the exact #111 drift shape (README claimed
# "six alerts are live today" while prometheus.yaml carried 14, four
# with no runbook at all), mechanically enforced this time instead of
# relying on a human cross-check to catch it again.
#
# Checking the README's own table (not a hardcoded "N alerts" sentence
# count) is deliberate: a free-text count claim is exactly the kind of
# prose staleness the 2026-08-09 review's own §7.1-3 found three more
# instances of elsewhere in this project, in a file with no mechanical
# check on it at all. A table row is structurally tied to a real alert
# name, not a number that can drift independently of the content next
# to it.

PLATFORM_DIR="${1:?usage: check-runbook-coverage.sh <platform-repo-path> [<runbooks-dir>] [<readme-path>]}"
RUNBOOKS_DIR="${2:-runbooks}"
README="${3:-$RUNBOOKS_DIR/README.md}"

if ! command -v yq >/dev/null 2>&1; then
  echo "yq is required (https://github.com/mikefarah/yq)" >&2
  exit 1
fi

PROM_APP="$PLATFORM_DIR/argocd/apps/prometheus.yaml"
if [ ! -f "$PROM_APP" ]; then
  echo "::error::$PROM_APP not found" >&2
  exit 1
fi

fail=0
alert_names=$(yq eval '.spec.source.helm.valuesObject.serverFiles["alerting_rules.yml"]' "$PROM_APP" \
  | yq eval '.groups[].rules[].alert // ""' - \
  | grep -v '^$' || true)

if [ -z "$alert_names" ]; then
  echo "No alert names extracted from $PROM_APP -- refusing to pass on an empty check." >&2
  exit 1
fi

while IFS= read -r name; do
  runbook="$RUNBOOKS_DIR/$name.md"
  if [ ! -f "$runbook" ]; then
    echo "::error file=$runbook::live alert '$name' (platform/argocd/apps/prometheus.yaml) has no runbook file" >&2
    fail=1
  fi
  if ! grep -qF -- "$name" "$README"; then
    echo "::error file=$README::live alert '$name' is not listed in runbooks/README.md's own table" >&2
    fail=1
  fi
done <<< "$alert_names"

if [ "$fail" -ne 0 ]; then
  echo "One or more live alerts are missing a runbook or a README.md table row (backlog #22/#111/#117)." >&2
  exit 1
fi

echo "Every live alert in prometheus.yaml has a runbook file and a runbooks/README.md table entry."
