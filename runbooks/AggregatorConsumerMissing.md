# AggregatorConsumerMissing

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
absent(kafka_consumer_fetch_manager_records_lag{job="aggregator", topic=~"stock\\.price\\.tick|news\\.sentiment\\.scored"})
```

`for: 3m`, `severity: critical`.

Companion to `AggregatorConsumerLagHigh`, not a duplicate — that rule
alerts on a real, reported lag *value*; this one alerts on the metric's
own *absence*. Same shape `WorkersConsumerMissing` already established
for `workers` (backlog #76, `observability/chaos/03-consumer-lag.md`):
when the consumer isn't running at all, there is no series to read a
high number from — `absent()` is the correct primitive, since it fires
when zero series match, not when a matching series happens to read 0.

## What it means in practice

`aggregator` has reported no `kafka_consumer_fetch_manager_records_lag`
series for its two real input topics for 3 minutes — almost certainly
the pod isn't running or isn't being scraped at all, not that lag is
genuinely zero. `for: 3m` is short enough to catch a genuinely
stuck-down consumer in minutes, matching the same reasoning
`WorkersConsumerMissing` states for its own window.

## First response

1. Confirm the pod's real state directly — this is the check the alert
   itself exists to make automatic:
   ```
   kubectl get pods -n aggregator
   ```
2. If the pod is crash-looping, check for the real, terminal
   `KafkaStreams.State.ERROR` shape backlog #85's two incidents both
   had (RocksDB/Alpine `libstdc++`, or a RocksDB metrics-wiring bug) —
   both fixed, but any *new* fatal cause would show the same shape:
   ```
   kubectl logs -n aggregator deploy/aggregator --tail=200 --previous
   ```
3. If the pod is up but still reports no series, confirm Prometheus's
   own scrape target for it is actually healthy (this exact gap — a
   real, live-confirmed missing scrape target — is why this alert and
   its sibling exist at all, see backlog #90):
   ```
   curl -s 'http://localhost:9090/api/v1/targets' | grep -A5 '"job":"aggregator"'
   ```

## How to confirm resolution

1. Confirm real series are reporting again:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=kafka_consumer_fetch_manager_records_lag{job="aggregator", topic=~"stock\\.price\\.tick|news\\.sentiment\\.scored"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c AggregatorConsumerMissing
   ```
