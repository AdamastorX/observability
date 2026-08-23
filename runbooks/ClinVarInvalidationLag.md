# ClinVarInvalidationLag

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
increase(clinvar_ingestion_jobs_total{job="clinvar-service", status="succeeded"}[1h])
  > increase(spring_kafka_listener_seconds_count{job="api", messaging_source_name="clinvar.ingestion.completed", error="none"}[1h])
```

`for: 15m`, `severity: critical`.

`clinvar-service` has completed more successful ingestions in the last
hour than `api`'s `clinvar.ingestion.completed` consumer has
successfully processed. Backlog #29 (ADR 0019/0020): `clinvar-service`
owns the release diff/publish step end-to-end; `api` only drains a
Kafka consumer and deletes the Redis keys it's told to. This alert's
failure surface is intentionally "did the publish happen, did the
consumer drain it" — not "did api recompute the diff correctly" (that
recompute no longer exists, per ADR 0019).

**Known, stated limitation** (not fixed by this runbook): `clinvar-service`'s
Kafka producer (`services/clinvar-service/app/kafka_producer.py`) has
no delivery-success/failure Prometheus metric of its own —
`confluent-kafka`'s `produce()`/`flush()` don't raise on an async
delivery failure, and the delivery callback only logs. A publish that
silently fails to reach the broker still leaves the ingestion job
recorded as `status="succeeded"`. This rule therefore cannot cleanly
tell you *which* half failed (publish vs. drain) from the expression
alone — see First response step 1 to disambiguate by hand. A real
publish-success/failure counter on `clinvar-service`'s side would close
this gap; not built as part of backlog #29 (services-repo scope).

## What it means in practice

`api` may be serving a stale cached variant-annotation answer for a
variant ClinVar has already reclassified — a correctness bug with a
clinical-safety framing, not a performance nicety (ADR 0018). Either:

- `clinvar-service` finished an ingestion and attempted to publish
  `clinvar.ingestion.completed`, but the message never reached the
  broker (a silent delivery failure, see the limitation above), or
- The message was published and durably committed to Kafka, but `api`'s
  consumer never processed it (consumer down, stuck, or erroring/DLQ'd
  every attempt).

## First response

1. Disambiguate publish vs. drain — check `clinvar-service`'s own logs
   first, since a delivery failure is only visible there today:
   ```
   kubectl logs -n clinvar deploy/clinvar-service --tail=200 | grep -i "Failed to deliver"
   ```
   A hit here means the publish side failed — treat as a `clinvar-service`
   Kafka connectivity problem (check `kubectl get pods -n kafka` first,
   the same real cause seen in this project's own Kafka incidents).
2. If no delivery-callback error appears, check `api`'s consumer side —
   confirm the pod is up and actually consuming, not just running:
   ```
   kubectl get pods -n api
   kubectl logs -n api deploy/api --tail=200 | grep -i "clinvar"
   ```
3. Check whether messages are landing in the dead-letter topic instead
   of being processed (`ClinVarCacheInvalidationConsumerConfig`'s
   `DeadLetterPublishingRecoverer` retries twice with a 1s backoff, then
   DLQs):
   ```
   kubectl exec -n kafka kafka-controller-0 -- kafka-console-consumer.sh \
     --bootstrap-server localhost:9092 --topic clinvar.ingestion.completed.DLT \
     --from-beginning --max-messages 10
   ```
   A non-empty DLQ means the consumer *is* running but erroring on the
   payload itself (e.g. a Redis outage during the delete step) — check
   `api`'s Redis (`redis-master-0` in the `api` namespace) is reachable.
4. Manual targeted mitigation while the root cause is being fixed: the
   event carries the exact Redis keys to delete (`changedKeys`) — if you
   can recover them from the DLQ payload or `clinvar-service`'s own logs
   (`"evicted %d of %d changed keys"` line, `VariantInvalidationService`),
   delete them directly against `api`'s Redis rather than waiting:
   ```
   kubectl exec -n api redis-master-0 -- redis-cli DEL 'variantAnnotation:<chrom>:<pos>:<ref>:<alt>'
   ```
   Cross-check with a live `GET /variants/lookup` call afterward to
   confirm the stale answer is gone.

## How to confirm resolution

1. Re-query live Prometheus directly:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=increase(clinvar_ingestion_jobs_total{job="clinvar-service",status="succeeded"}[1h]) > increase(spring_kafka_listener_seconds_count{job="api",messaging_source_name="clinvar.ingestion.completed",error="none"}[1h])'
   ```
   Should return an empty result (the condition no longer true) once
   the two counts are back in step.
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ClinVarInvalidationLag
   ```
3. Confirm the actual correctness property, not just the metric: pick a
   variant `changedKeys` named in the delayed event and confirm a live
   `GET /variants/lookup` now reflects the new classification, not a
   still-cached stale one.
