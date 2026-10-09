#!/usr/bin/env bash
set -euo pipefail

# backlog #143/#144 (ADR 0046's bounded-verification-loop discipline):
# `promtool check rules` validates PromQL syntax at render time;
# `promtool test rules` unit-tests the alert *logic* itself against
# synthetic time series -- neither needs a live Prometheus. Three real
# defects of exactly this class already shipped undetected before any
# of this existed: #91's WEBSOCKET/POLL_FALLBACK blend trap, #145's
# selector that rendered nothing, and #143's own staleness gate (an
# exitcode==137 gauge that, without a recency bound, paged forever
# after the pod it fired on had long since recovered).
#
# The real rules live embedded in platform/argocd/apps/prometheus.yaml
# (serverFiles["alerting_rules.yml"], a Helm valuesObject string, not a
# PrometheusRule CRD) -- extracted here the same cross-repo-checkout
# way check-runbook-coverage.sh (#117) already reads that file.

PLATFORM_DIR="${1:?usage: check-prometheus-rules.sh <platform-repo-path> <extracted-rules-out> <test-file>}"
EXTRACTED="${2:?usage: check-prometheus-rules.sh <platform-repo-path> <extracted-rules-out> <test-file>}"
TEST_FILE="${3:?usage: check-prometheus-rules.sh <platform-repo-path> <extracted-rules-out> <test-file>}"

if ! command -v yq >/dev/null 2>&1; then
  echo "yq is required (https://github.com/mikefarah/yq)" >&2
  exit 1
fi
if ! command -v promtool >/dev/null 2>&1; then
  echo "promtool is required (https://github.com/prometheus/prometheus/releases)" >&2
  exit 1
fi

PROM_APP="$PLATFORM_DIR/argocd/apps/prometheus.yaml"
if [ ! -f "$PROM_APP" ]; then
  echo "::error::$PROM_APP not found" >&2
  exit 1
fi

yq eval '.spec.source.helm.valuesObject.serverFiles["alerting_rules.yml"]' "$PROM_APP" > "$EXTRACTED"

if [ ! -s "$EXTRACTED" ] || [ "$(cat "$EXTRACTED")" = "null" ]; then
  echo "::error::extracted no real alerting_rules.yml content from $PROM_APP -- the valuesObject path may have moved" >&2
  exit 1
fi

echo "== promtool check rules (PromQL syntax) =="
promtool check rules "$EXTRACTED"

echo "== promtool test rules (alert logic, against synthetic series) =="
promtool test rules "$TEST_FILE"
