# WorkersConsumerMissing

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
absent(kafka_consumer_fetch_manager_records_lag{job="workers"})
```

`for: 3m`, `severity: critical`.

Companion to `WorkersConsumerLagHigh`, not a duplicate — that rule
alerts on a real, reported lag *value*; this one alerts on the
metric's own *absence*. Found live during backlog #23's chaos scenario
3 (`observability/chaos/03-consumer-lag.md`): `workers` scaled to
zero (KEDA, backlog #63) leaves no process to report
`kafka_consumer_fetch_manager_records_lag` at all — the metric doesn't
show a large number, it shows nothing, which `WorkersConsumerLagHigh`
structurally cannot detect. `absent()` is the correct primitive here,
since it fires when zero series match, not when a matching series
happens to read a low number.

## What it means in practice

`workers` has reported no `kafka_consumer_fetch_manager_records_lag`
series for 3 minutes — almost certainly the pod isn't running or isn't
being scraped at all, not that lag is genuinely zero. `for: 3m` is
short enough to catch a genuinely stuck-down consumer in minutes.

**Check KEDA before assuming an incident**: `workers` is
event-driven-autoscaled on real Kafka consumer lag (backlog #63).
Today `minReplicaCount: 1` (scale-to-zero deliberately not adopted
yet, backlog #113 — no signal existed to distinguish "KEDA correctly
scaled to zero" from "a real stuck consumer" when that decision was
made), so a healthy, intentional KEDA scale-to-zero should not be
possible right now. If `#113` is later adopted, re-read this runbook's
own updated version before assuming every firing of this alert is a
real incident.

## First response

1. Confirm the pod's real state directly — this is the check the
   alert itself exists to make automatic:
   ```
   kubectl get pods -n workers
   ```
2. If no pod exists at all, check KEDA's own `ScaledObject` and HPA
   state — did it scale to 0 unexpectedly, or is it stuck computing a
   desired replica count:
   ```
   kubectl get scaledobject -n workers
   kubectl get hpa -n workers
   kubectl describe hpa -n workers keda-hpa-workers
   ```
3. If a pod exists but is crash-looping, check its own logs for the
   real cause:
   ```
   kubectl logs -n workers deploy/workers --tail=200 --previous
   ```
4. If the pod is up but still reports no series, confirm Prometheus's
   own scrape target for it is actually healthy — `workers` has no
   Kubernetes Service (ADR 0009/0011), scraped via Kubernetes pod-role
   service discovery instead of a static target, a real, different
   failure surface from a static scrape config:
   ```
   curl -s 'http://localhost:9090/api/v1/targets' | grep -A5 '"job":"workers"'
   ```
5. If the topic/consumer group itself looks wrong, check for the known
   topic-wipe-on-broker-restart behavior (ADR 0011, superseded by ADR
   0032's persistent storage — this should no longer recur post-#95,
   but confirm the fix is actually live if this alert fires):
   ```
   kubectl get pods -n kafka
   kubectl exec -n kafka kafka-controller-0 -- kafka-consumer-groups.sh \
     --bootstrap-server localhost:9092 --describe --group workers
   ```

## How to confirm resolution

1. Confirm real series are reporting again:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=kafka_consumer_fetch_manager_records_lag{job="workers"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c WorkersConsumerMissing
   ```
