# otel

Design notes for the OpenTelemetry Collector's pipeline (observability#1,
ADR 0013). The deployable manifest — the ArgoCD Application and its
inline pipeline config — lives in `platform`:
[`argocd/apps/otel-collector.yaml`](https://github.com/AdamastorX/platform/blob/main/argocd/apps/otel-collector.yaml).
ArgoCD only watches `platform` (ADR 0003), so that file is the deployed
source of truth; this one documents *why* it's shaped that way, the same
"pointer, not a second copy" pattern the project's `.claude/` cross-repo
context already uses.

## What's collected today

Traces only. `gateway`, `api`, and `workers` each push spans via OTLP
HTTP (`micrometer-tracing-bridge-otel`, 100% sampled) to the Collector,
which batches them and — for now — just logs them (`debug` exporter).
That's enough to prove a trace crosses all three services without
needing a real trace backend built yet.

Metrics stay on each service's own `/actuator/prometheus` scrape
endpoint rather than being routed through this Collector — backlog #18
(Prometheus/Grafana) scrapes directly, so pushing metrics through OTLP
first would add a hop nothing here uses. Logs stay as plain console
output; centralizing them is backlog #19 (Loki), a deliberately separate
step.

## What's deferred, and the trigger to revisit

- **A real trace backend (Tempo, backlog #19)**: swap the `debug`
  exporter for an OTLP-to-Tempo one — small diff to the pipeline config,
  not a redesign.
- **Metrics/logs through this Collector**: only if a signal genuinely
  needs push instead of pull/scrape (e.g. a short-lived batch job
  Prometheus can't scrape) — not scheduled speculatively.
- **The chart's unused `jaeger`/`zipkin` receivers**: left in place
  (inert, ClusterIP-only) rather than fought out of the config via
  Helm's null-based trimming for a marginal cleanup. Remove them if the
  values file is being touched for another reason anyway, not as a
  dedicated cleanup.
