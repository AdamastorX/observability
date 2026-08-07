# SentimentAnalyzerConsumerLagHigh

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
sum(sentiment_analyzer_consumer_lag{job="sentiment-analyzer"}) > 20
```

`for: 10m`, `severity: warning`.

`sentiment-analyzer`'s `news.article.published` consumer backlog has
stayed above 20 un-consumed records for 10 minutes.

`sentiment_analyzer_consumer_lag` (backlog #90) did not exist before
this item: unlike the Java services on this cluster, where Micrometer's
`KafkaStreamsMetrics`/`KafkaClientMetrics` binders expose real consumer
lag for free, `confluent-kafka`'s Python client has no automatic
Prometheus export. This Gauge is fed from librdkafka's own
`statistics.interval.ms`/`stats_cb` mechanism — real per-partition lag
librdkafka computes internally, not derived independently by this
project's own code.

## What it means in practice

`sentiment-analyzer` is falling behind `news.article.published`'s
production rate. 20 records is small on purpose: this pipeline's real
traffic (matched financial-news articles) is naturally sparse — see
`news-ingestor`'s own real counters (`matched`/`published` per poll
cycle) for the actual observed scale this threshold is set against.

## First response

1. Check if `sentiment-analyzer` is even running/consuming at all:
   ```
   kubectl get pods -n sentiment-analyzer
   kubectl logs -n sentiment-analyzer deploy/sentiment-analyzer --tail=100
   ```
2. Check `SentimentConsumerWorker.is_alive()`'s own real failure mode —
   an uncaught exception inside the consume loop can kill the daemon
   thread while `/healthz` still reports `UP` unless this specific
   guard catches it (see that method's own docstring for the incident
   this was built to prevent):
   ```
   curl -s http://localhost:8000/healthz  # via port-forward
   ```
3. Confirm the consumer group is actually attached to real partitions:
   ```
   kubectl exec -n kafka kafka-controller-0 -- kafka-consumer-groups.sh \
     --bootstrap-server localhost:9092 --describe --group sentiment-analyzer
   ```

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sum(sentiment_analyzer_consumer_lag{job="sentiment-analyzer"})'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c SentimentAnalyzerConsumerLagHigh
   ```
3. Cross-check with `kafka-consumer-groups.sh --describe` directly for
   the real `LAG` column, same reasoning `WorkersConsumerLagHigh`'s own
   runbook states — confirms it's actually draining, not just a
   momentary scrape dip.
