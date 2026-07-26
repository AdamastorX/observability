# WorkersListenerErrorRate

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(
  sum(rate(spring_kafka_listener_seconds_count{job="workers", error!="none"}[5m]))
  /
  sum(rate(spring_kafka_listener_seconds_count{job="workers"}[5m]))
) > 0.05
and sum(rate(spring_kafka_listener_seconds_count{job="workers"}[5m])) > 0
```

`for: 5m`, `severity: critical`.

More than 5% of `workers`' `work-items` listener invocations over a
sustained 5 minutes have completed with a non-`none` `error` tag (the
tag itself names the exception class). See ADR 0020's SLO table.

## What it means in practice

`workers` is failing to process a real share of `work-items` messages.
`workers` has no HTTP API of its own (ADR 0009/0011), so this is only
visible via this metric or its logs — there's no request path to curl
to reproduce it directly.

## First response

1. Check `workers`' own logs for the actual exception the `error` tag is
   naming — check for a poison message (one specific message repeatedly
   failing) vs. a downstream dependency outage:
   ```
   kubectl logs -n workers deploy/workers --tail=200
   ```
2. **Check for the Postgres Secret drift failure mode** (platform#34/#40)
   if the logs show a connection/auth error — `workers` shares the same
   `api`-namespace Postgres instance, and a Secret can silently diverge
   from the running database's actual password even with
   `ignoreDifferences` in place (which only prevents *future* drift, not
   a pre-existing mismatch found on a fresh pod's first connection):
   ```
   kubectl exec -n api postgresql-0 -- env | grep PASSWORD
   kubectl get secret postgresql -n api -o jsonpath='{.data.password}' | base64 -d
   ```
3. Confirm `workers` is actually running and connected to Kafka at all,
   not just slow:
   ```
   kubectl get pods -n workers
   kubectl get pods -n kafka
   ```
4. If the error looks Kafka-side rather than a processing bug, check
   whether the `work-items` topic itself still exists — topics don't
   survive a broker pod restart on this cluster's ephemeral storage
   (ADR 0011, `auto.create.topics.enable: false`), seen before after a
   coincidental `kafka-controller-0` restart wiped all three topics:
   ```
   kubectl exec -n kafka kafka-controller-0 -- kafka-topics.sh \
     --bootstrap-server localhost:9092 --list
   ```
   If `work-items` is missing, that's the root cause, not a listener bug
   — recreate it manually (3 partitions, replication factor 1) and
   restart `api`/`workers` for clean consumer-group state.

## How to confirm resolution

1. Re-query the same ratio against live Prometheus and confirm it's back
   under 0.05:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=
     sum(rate(spring_kafka_listener_seconds_count{job="workers", error!="none"}[5m]))
     /
     sum(rate(spring_kafka_listener_seconds_count{job="workers"}[5m]))'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c WorkersListenerErrorRate
   ```
3. Produce a real test message onto `work-items` and confirm `workers`'
   logs show it processed cleanly (no new `error!=none` timer entry).
