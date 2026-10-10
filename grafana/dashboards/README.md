# grafana/dashboards

Design notes for the three golden-signal dashboards (observability#4,
backlog #20, ADR 0017 in `adamastorx`) — one dashboard per service
(`gateway`, `api`, `workers`), each covering latency, traffic, errors,
and saturation. The deployable dashboard JSON lives in `platform`:
[`argocd/apps/grafana.yaml`](https://github.com/AdamastorX/platform/blob/main/argocd/apps/grafana.yaml)'s
`dashboardProviders`/`dashboards` Helm values. ArgoCD only watches
`platform` (ADR 0003), so that file is the deployed source of truth;
this one documents *why* the dashboards are shaped the way they are —
same "pointer, not a second copy" pattern
[`../../otel/README.md`](../../otel/README.md) already uses for the OTel
Collector's pipeline config.

**These dashboards ship with no alerts and no SLOs yet.** The
`observability-engineer` persona's own rule is "never ships a dashboard
without the alert/SLO it's meant to support" — read literally, this
issue breaks that rule, since backlog #21 ("Define SLOs and alerting
rules") comes *after* this one and depends on it. ADR 0017 works through
that tension explicitly: these four signals are the standard precursor
to SLO definition, not unrelated dashboard sprawl, and #21 is the very
next planned item, not deferred indefinitely. Nothing here pretends the
gap doesn't exist — see ADR 0017's "A tension worth resolving
explicitly, not silently" section for the full reasoning.

## What's on each dashboard

`gateway` and `api` are HTTP-facing (Boot's `http_server_requests_seconds_*`
metrics); `workers` has no business HTTP API (ADR 0009/0011 — confirmed
again here by querying a live target, its only `http_server_requests_seconds`
series are `/actuator/health` and `/actuator/prometheus`) so its signals
come from the `spring.kafka.listener` timer instead. Every query below
was checked against a live Prometheus (`kubectl port-forward` to
`prometheus-server`, read-only) before being written into the dashboard
JSON — metric and label names weren't assumed from Micrometer/Spring-Kafka
docs.

| Signal | `gateway` / `api` | `workers` |
|---|---|---|
| Traffic | `rate(http_server_requests_seconds_count{uri!~"/actuator.*"}[..])` | `rate(spring_kafka_listener_seconds_count[..])` |
| Latency | avg (`rate(_sum)/rate(_count)`) + max, both from `http_server_requests_seconds_*` | avg + max, both from `spring_kafka_listener_seconds_*` |
| Errors | `outcome="SERVER_ERROR"` rate vs. total, plus a ratio stat panel | the timer's own `error` label (`!="none"`) rate vs. total, plus a ratio stat panel |
| Saturation | JVM heap used/max; `api` also gets HikariCP pool usage (Postgres, ADR 0012), `gateway` gets process CPU instead | JVM heap used/max, plus `applicationTaskExecutor` thread-pool usage as a stated proxy (see gap below) |

Deliberately **not** one parameterized dashboard with a `$service`
variable across all three — the signal *sources* genuinely differ
(HTTP timer vs. Kafka listener timer), so a shared template would
either hide that or force artificial symmetry. Per-service panels also
means `api`'s HikariCP panel and `workers`' executor-pool panel aren't
copy-pasted filler — they're the saturation signal that's actually
meaningful for that service.

## Two known gaps, stated here rather than silently worked around

- **No true latency percentiles.** Boot doesn't export
  `http_server_requests_seconds_bucket` (or the Kafka timer's bucket
  equivalent) here — `management.metrics.distribution.percentiles-histogram`
  isn't enabled on any of the three services. Confirmed by listing
  every series each job exports and finding no `_bucket` suffix
  anywhere. The latency panels show average (`sum/count`) and max only,
  not p95/p99. Fixing this is a Micrometer config change in `services`,
  out of scope for a dashboard-only PR — the trigger to revisit is
  backlog #21 wanting a real percentile-based SLO.
- **No Kafka consumer-lag metric for `workers`.** The textbook
  saturation signal for a Kafka consumer is lag
  (`kafka_consumer_fetch_manager_records_lag_max` or similar, from a
  Micrometer/Kafka-client metrics binder) — not exposed today,
  confirmed by listing every series `workers` exports. The dashboard
  uses `applicationTaskExecutor` thread-pool usage as a proxy instead,
  and the panel's own `description` field says so directly in the
  Grafana UI, not just in this doc. Wiring a real lag metric is an
  app-level change in `services`; the trigger to revisit is the proxy
  visibly failing to correlate with real backlog during a future
  chaos-test scenario (backlog #23).

## Provisioning mechanism

File-based provisioning (`dashboardProviders` + `dashboards` Helm
values on the `grafana-community/grafana` chart), not the chart's
sidecar (`sidecar.dashboards.enabled`, a `k8s-sidecar` container
watching labeled ConfigMaps cluster/namespace-wide). Verified by
pulling the exact chart/version `platform/argocd/apps/grafana.yaml`
already sources and rendering both mechanisms' templates locally before
choosing: the values-driven approach generates one ConfigMap per
provider and mounts each dashboard's JSON directly into the Grafana
pod via `subPath` — no extra container, no extra RBAC, appropriate for
content that's fully known at Helm-values (GitOps commit) time. The
sidecar solves a different problem — dashboards owned by other
workloads dropping labeled ConfigMaps independently of Grafana's own
deploy — that doesn't exist here. Full reasoning in ADR 0017.

## SLO dashboards: the standard (backlog #177, M20)

The Golden Signals dashboards above answer "what is happening". The SLO
dashboards answer one question per row: **is this meeting its target right
now?** They sit in Grafana's `SLO` folder and are generated by
[`platform/scripts/gen-slo-dashboards.py`](https://github.com/AdamastorX/platform/blob/main/scripts/gen-slo-dashboards.py)
(platform CI fails if `argocd/apps/grafana.yaml` drifts from it). The
Golden Signals dashboards stay as the drill-down.

Rules every SLO dashboard follows:

1. **One overview, every SLO visible.** The `SLO Overview` dashboard shows
   every SLO ADR 0020 defines as one tile, grouped by section (ClinVar
   lookup, market-data pipeline, platform), with three counters on top:
   meeting target, breached, no data. Nobody should have to open several
   dashboards to count the SLOs.
2. **Status first.** Each tile has a coloured background and a small
   sparkline inside it: green inside the target, amber close to it, red
   breached, **grey for no traffic or no data**. Never green for "nothing
   to measure". No empty or decorative panels.
3. **Plain language.** Titles read as a result ("API requests succeeding"),
   not a metric name; units are explicit; each tile's description names the
   target and the alert it mirrors.
4. **The same expression as the alert.** A tile uses the alert's own
   ratio and window (5 minutes). A dashboard that disagrees with its alert is
   a bug. Where no alert exists (for example api's p95, which today is only
   the canary's `api-slo-check` threshold) the description says the
   threshold is preliminary.
5. **Honest windows.** Tiles show the current state. No error-budget or
   28/30-day figure until #94's retention window has closed; Prometheus
   keeps 3 days and Mimir about 6.
6. **A link to the detail.** Clicking a tile opens the matching Golden
   Signals dashboard.

Adding an SLO means adding one entry to `SLOS` in the generator.
