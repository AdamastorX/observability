# runbooks

One runbook per alert defined in `platform/argocd/apps/prometheus.yaml`'s
`server.serverFiles.alerting_rules.yml` (backlog #21, ADR 0020) — the
project's alert rules live there as Prometheus's own static config, not as
a separate `grafana/alerts/` artifact in this repo.

Six alerts are live today, each with a runbook below covering what fired,
what it means, first-response steps grounded in this project's own real
incident history, and how to confirm resolution:

| Alert | Severity | Runbook |
|---|---|---|
| `ApiHighErrorRate` | critical | [ApiHighErrorRate.md](./ApiHighErrorRate.md) |
| `ApiVariantsLookupHighErrorRate` | critical | [ApiVariantsLookupHighErrorRate.md](./ApiVariantsLookupHighErrorRate.md) |
| `WorkersListenerErrorRate` | critical | [WorkersListenerErrorRate.md](./WorkersListenerErrorRate.md) |
| `WorkersConsumerLagHigh` | warning | [WorkersConsumerLagHigh.md](./WorkersConsumerLagHigh.md) |
| `ClinVarIngestionFreshnessBreach` | critical | [ClinVarIngestionFreshnessBreach.md](./ClinVarIngestionFreshnessBreach.md) |
| `ClinVarIngestionDurationAnomaly` | warning | [ClinVarIngestionDurationAnomaly.md](./ClinVarIngestionDurationAnomaly.md) |

A seventh rule, `GatewayHighErrorRate`, was written alongside these but is
not a live alert: the `gateway` service it scraped was removed entirely in
ADR 0021/backlog #S1's simplification pass, and the rule (and its scrape
target) was removed from `prometheus.yaml` in the same cleanup. No runbook
exists for it.

Alerts route to a real notification channel (backlog #21c) — a webhook
receiver pointed at a dedicated `ntfy.sh` topic, wired in Alertmanager's
`config` in the same `prometheus.yaml`. `critical`-severity alerts get a
faster `group_wait`/`repeat_interval` than `warning`-severity ones.

`clinvar-service`'s lookup-path non-5xx-rate alert (ADR 0020's SLO table
names it) has no runbook yet because it hasn't shipped — the underlying
metric has no status/outcome label to alert against (tracked as backlog
#21e). No runbook is written ahead of an alert that doesn't exist yet.
