# runbooks

One runbook per alert defined in `platform/argocd/apps/prometheus.yaml`'s
`server.serverFiles.alerting_rules.yml` (backlog #21, ADR 0020) — the
project's alert rules live there as Prometheus's own static config, not as
a separate `grafana/alerts/` artifact in this repo.

This table's own count ("Six alerts are live today") went stale without
anyone noticing — found live while adding backlog #90's four new
runbooks (2026-08-07): `prometheus.yaml` carries 18 real alert rules
today (the 14 from that original count, plus `NodeDiskSpaceLow`/
`NodeDiskSpaceCritical` from backlog #92, `BlackboxProbeFailing` from
backlog #93, and `AggregatorPriceFreshnessSlow` from backlog #91).
The real gap this drift hid was stated honestly rather than silently
patched at the time: four existing alerts (`WorkersConsumerMissing`,
`WatchlistDlqDepthHigh`, `MarketDataStaleFeed`, `ApiRateLimitRejectionsHigh`)
had no runbook at all — a direct violation of backlog #22's own "one
runbook per alert" rule. Tracked separately as adamastorx backlog
#111 and closed there (2026-08-08) — all four now have a real runbook
below.

| Alert | Severity | Runbook |
|---|---|---|
| `ApiHighErrorRate` | critical | [ApiHighErrorRate.md](./ApiHighErrorRate.md) |
| `ApiVariantsLookupHighErrorRate` | critical | [ApiVariantsLookupHighErrorRate.md](./ApiVariantsLookupHighErrorRate.md) |
| `WorkersListenerErrorRate` | critical | [WorkersListenerErrorRate.md](./WorkersListenerErrorRate.md) |
| `WorkersConsumerLagHigh` | warning | [WorkersConsumerLagHigh.md](./WorkersConsumerLagHigh.md) |
| `WorkersConsumerMissing` | critical | [WorkersConsumerMissing.md](./WorkersConsumerMissing.md) |
| `ClinVarIngestionFreshnessBreach` | critical | [ClinVarIngestionFreshnessBreach.md](./ClinVarIngestionFreshnessBreach.md) |
| `ClinVarIngestionDurationAnomaly` | warning | [ClinVarIngestionDurationAnomaly.md](./ClinVarIngestionDurationAnomaly.md) |
| `WatchlistDlqDepthHigh` | warning | [WatchlistDlqDepthHigh.md](./WatchlistDlqDepthHigh.md) |
| `MarketDataStaleFeed` | warning | [MarketDataStaleFeed.md](./MarketDataStaleFeed.md) |
| `ApiRateLimitRejectionsHigh` | warning | [ApiRateLimitRejectionsHigh.md](./ApiRateLimitRejectionsHigh.md) |
| `AggregatorConsumerLagHigh` (backlog #90) | warning | [AggregatorConsumerLagHigh.md](./AggregatorConsumerLagHigh.md) |
| `AggregatorConsumerMissing` (backlog #90) | critical | [AggregatorConsumerMissing.md](./AggregatorConsumerMissing.md) |
| `SentimentAnalyzerConsumerLagHigh` (backlog #90) | warning | [SentimentAnalyzerConsumerLagHigh.md](./SentimentAnalyzerConsumerLagHigh.md) |
| `SentimentAnalyzerConsumerMissing` (backlog #90) | critical | [SentimentAnalyzerConsumerMissing.md](./SentimentAnalyzerConsumerMissing.md) |
| `NodeDiskSpaceLow` (backlog #21d/#92) | warning | [NodeDiskSpaceLow.md](./NodeDiskSpaceLow.md) |
| `NodeDiskSpaceCritical` (backlog #21d/#92) | critical | [NodeDiskSpaceCritical.md](./NodeDiskSpaceCritical.md) |
| `BlackboxProbeFailing` (backlog #93) | critical | [BlackboxProbeFailing.md](./BlackboxProbeFailing.md) |
| `AggregatorPriceFreshnessSlow` (backlog #91) | warning | [AggregatorPriceFreshnessSlow.md](./AggregatorPriceFreshnessSlow.md) |

A nineteenth rule, `GatewayHighErrorRate`, was written alongside these but
is not a live alert: the `gateway` service it scraped was removed
entirely in ADR 0021/backlog #S1's simplification pass, and the rule
(and its scrape target) was removed from `prometheus.yaml` in the same
cleanup. No runbook exists for it.

Alerts route to a real notification channel (backlog #21c) — a webhook
receiver pointed at a dedicated `ntfy.sh` topic, wired in Alertmanager's
`config` in the same `prometheus.yaml`. `critical`-severity alerts get a
faster `group_wait`/`repeat_interval` than `warning`-severity ones.

`clinvar-service`'s lookup-path non-5xx-rate alert (ADR 0020's SLO table
names it) has no runbook yet because it hasn't shipped — the underlying
metric has no status/outcome label to alert against (tracked as backlog
#21e). No runbook is written ahead of an alert that doesn't exist yet.
