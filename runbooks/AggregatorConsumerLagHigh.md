# AggregatorConsumerLagHigh

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
sum(kafka_consumer_fetch_manager_records_lag{job="aggregator", topic=~"stock\\.price\\.tick|news\\.sentiment\\.scored"}) > 20
```

`for: 10m`, `severity: warning`.

`aggregator`'s combined backlog across its two real input topics
(`stock.price.tick`, `news.sentiment.scored`) has stayed above 20
un-consumed records for 10 minutes. The topic filter is deliberate and
load-bearing, not cosmetic: Kafka's client metrics reporter emits this
same real lag twice per topic on this cluster — once under the real
dotted topic name, once under a mangled underscore variant
(`stock_price_tick`) — confirmed live before this rule was written, not
assumed. The `\.` in the regex matches a literal dot, excluding the
duplicate. The internal state-store changelog/restore-consumer topics
(`aggregator-*-store-changelog`) are deliberately excluded too — those
spike by design on every restart (see `StateStoreRecoveryTest`'s real
~45s window, `aggregator/README.md`) and are `aggregator_state_restore_duration_seconds`'s
job, not this alert's.

## What it means in practice

`aggregator` (a Kafka Streams app) is falling behind its two real input
topics — a backlog signal, not a latency one, same reasoning
`WorkersConsumerLagHigh` states for `workers`. 20 records is small on
purpose: this pipeline's real per-15-minute-window volume is a handful
to a few dozen ticks/scores per ticker (backlog #87's own live-verified
numbers), two orders of magnitude below `work-items`' traffic — the
threshold is recalibrated to this pipeline's actual scale, not copied
from `workers`' own 500.

## First response

1. **Check if `aggregator` is even running first**, before assuming it's
   just slow:
   ```
   kubectl get pods -n aggregator
   kubectl logs -n aggregator deploy/aggregator --tail=100
   ```
2. Check for the real, terminal `KafkaStreams.State.ERROR` this
   pipeline has hit before (backlog #85) — `KafkaStreamsLivenessHealthIndicator`
   (backlog #85(b)) should already have flipped `/actuator/health/liveness`
   unhealthy and triggered a real pod restart before this alert fires;
   if it's firing anyway, either that mechanism didn't catch this
   specific failure mode or the restart itself is looping:
   ```
   curl -s http://localhost:8080/actuator/health/liveness  # via port-forward
   kubectl get pods -n aggregator -o wide
   ```
3. Confirm the consumer group is actually attached to real partitions:
   ```
   kubectl exec -n kafka kafka-controller-0 -- kafka-consumer-groups.sh \
     --bootstrap-server localhost:9092 --describe --group aggregator
   ```
4. If the topic itself looks wrong (missing partitions, group not
   found), check for the known topic-wipe-on-broker-restart behavior
   (ADR 0011, and its re-decision tracked in backlog #95) — a broker
   pod restart drops all topics on this cluster's ephemeral storage:
   ```
   kubectl get pods -n kafka
   kubectl exec -n kafka kafka-controller-0 -- kafka-topics.sh \
     --bootstrap-server localhost:9092 --describe --topic stock.price.tick
   ```

## How to confirm resolution

1. Re-query the same expression (with the same topic filter — a naive
   unfiltered query will read double) against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sum(kafka_consumer_fetch_manager_records_lag{job="aggregator", topic=~"stock\\.price\\.tick|news\\.sentiment\\.scored"})'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c AggregatorConsumerLagHigh
   ```
3. Cross-check `GET /aggregates` is returning current, non-stale
   `priceAsOf`/`sentimentAsOf` values (backlog #87) — the actual
   user-visible signal this alert protects.
