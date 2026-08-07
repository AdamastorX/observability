# AggregatorPriceFreshnessSlow

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
histogram_quantile(0.95, sum(rate(aggregator_price_freshness_seconds_bucket{source="WEBSOCKET"}[10m])) by (le)) > 30
```

`for: 10m`, `severity: warning`.

`aggregator_price_freshness_seconds` is the real end-to-end pipeline
freshness SLI (backlog #91) — the actual gap between a real trade's
Finnhub exchange timestamp and `aggregator`'s own Kafka Streams
topology processing the resulting `stock.price.tick` record. The p95
over the last 10 minutes has stayed above 30 seconds, on real
websocket-sourced ticks only.

**The `source="WEBSOCKET"` filter is deliberate and load-bearing, not
cosmetic.** `market-data-ingestor`'s `FinnhubQuotePoller` REST-poll
fallback publishes onto this same `stock.price.tick` topic, in this
same wire shape, every 30 minutes, by design, specifically to cover
off-hours/gaps (`source="POLL_FALLBACK"`). Its ticks carry a real,
correct ~30-minute exchange-timestamp-to-processed lag on every single
one — not a problem, the fallback doing its job. This alert can
structurally never fire on that: the query only ever reads the
`source="WEBSOCKET"` series.

## What it means in practice

A real trade tick is taking longer than expected to become visible via
`GET /aggregates`. The delay can sit in any of three real places:

1. **Upstream of Kafka** — `market-data-ingestor`'s own
   Finnhub-websocket-receipt-to-Kafka-send path (backlog #78's AC:
   "under 2s").
2. **Kafka itself** — broker produce/replication/consume latency.
3. **`aggregator`'s own consumption** — falling behind its consumer
   group (see `AggregatorConsumerLagHigh`, a related but distinct
   alert: that one fires on records piling up unconsumed; this one
   fires on the ones that *are* being consumed arriving stale).

## First response

1. Isolate which of the three stages is slow — check
   `market-data-ingestor`'s own publish-latency histogram first (rules
   out stage 1 quickly if it's healthy):
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=histogram_quantile(0.95, sum(rate(market_data_tick_publish_latency_seconds_bucket{job="market-data-ingestor"}[10m])) by (le))'
   ```
2. Check `aggregator`'s own consumer lag (rules out/confirms stage 3):
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sum(kafka_consumer_fetch_manager_records_lag{job="aggregator", topic=~"stock\\.price\\.tick"})'
   ```
   A high value here means `AggregatorConsumerLagHigh` should also be
   firing or about to — if it isn't, check the pod is actually running
   and its Kafka Streams thread isn't stuck (`State.ERROR`, backlog
   #85(b)'s `KafkaStreamsLivenessHealthIndicator` should already have
   restarted a genuinely dead thread before this alert's own 10m
   window elapses).
3. If neither of the above is elevated, the delay is most likely stage
   2 (Kafka itself) — check broker health directly:
   ```
   kubectl get pods -n kafka
   kubectl top pod -n kafka
   ```
4. Confirm real US market hours are actually in effect. Outside market
   hours, real websocket trade ticks stop arriving entirely (Finnhub's
   own real market-hours behavior, not a bug) — a stale *last* tick
   during closed hours is `MarketDataStaleFeed`'s job, not this one;
   this alert only evaluates while `aggregator_price_freshness_seconds`
   is actually being recorded, i.e. while real websocket ticks are
   still arriving and just arriving late.

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=histogram_quantile(0.95, sum(rate(aggregator_price_freshness_seconds_bucket{source="WEBSOCKET"}[10m])) by (le))'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c AggregatorPriceFreshnessSlow
   ```
3. Cross-check a real `GET /aggregates` response's own `priceAsOf`
   field (backlog #87) is recent — the actual user-visible signal this
   alert protects.
