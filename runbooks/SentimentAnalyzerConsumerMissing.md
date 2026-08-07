# SentimentAnalyzerConsumerMissing

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
absent(sentiment_analyzer_consumer_lag{job="sentiment-analyzer"})
```

`for: 3m`, `severity: critical`.

Companion to `SentimentAnalyzerConsumerLagHigh`, not a duplicate — that
rule alerts on a real, reported lag *value*; this one alerts on the
metric's own *absence*, the same `WorkersConsumerMissing`/
`AggregatorConsumerMissing` pattern applied to this service's consumer.

## What it means in practice

`sentiment-analyzer` has reported no `sentiment_analyzer_consumer_lag`
series at all for 3 minutes — almost certainly the pod isn't running,
isn't being scraped, or the consumer's daemon thread has died while the
process itself stayed up. `SentimentConsumerWorker.is_alive()`'s own
docstring documents the exact real incident this last case protects
against: an uncaught exception silently killing the consume loop while
`/healthz` kept reporting `UP` unconditionally, until that guard was
added.

## First response

1. Confirm the pod's real state directly:
   ```
   kubectl get pods -n sentiment-analyzer
   ```
2. If the pod is up, check whether the consumer thread itself is still
   alive (the specific failure mode this alert's sibling docstring
   names) — `/healthz` should already reflect a dead thread as
   unhealthy:
   ```
   curl -s http://localhost:8000/healthz  # via port-forward
   kubectl logs -n sentiment-analyzer deploy/sentiment-analyzer --tail=200
   ```
3. If the pod is crash-looping, check for an unhandled exception during
   startup (Kafka connection failure, missing config) rather than
   assuming the same shape as a mid-run thread death:
   ```
   kubectl logs -n sentiment-analyzer deploy/sentiment-analyzer --tail=200 --previous
   ```

## How to confirm resolution

1. Confirm real series are reporting again:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sentiment_analyzer_consumer_lag{job="sentiment-analyzer"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c SentimentAnalyzerConsumerMissing
   ```
