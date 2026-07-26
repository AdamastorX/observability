# WorkersConsumerLagHigh

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
sum(kafka_consumer_fetch_manager_records_lag{job="workers"}) > 500
```

`for: 10m`, `severity: warning`.

`workers`' `work-items` consumer group's total backlog (summed across
all 3 partitions, ADR 0011), has stayed above 500 un-consumed records
for a sustained 10 minutes. `10m`, longer than the HTTP/listener error
rules, deliberately — lag can spike transiently on a burst of produced
messages before `workers` catches back up; 10m of sustained backlog is
what separates "catching up" from "stuck". See ADR 0020's SLO table.

## What it means in practice

`workers` is falling behind `work-items`' production rate — a
saturation/backlog signal, not a latency one (a queue consumer's real
capacity problem shows up as backlog depth, ADR 0020). 500 records is
multiple orders of magnitude past this dev cluster's normal handful of
messages per session, so this is a genuine "consumer isn't keeping up"
signal, not noise.

## First response

1. **Check if `workers` is even running/consuming at all before
   assuming it's just slow** — this is the specific guidance ADR 0020's
   own rule comment gives for this alert:
   ```
   kubectl get pods -n workers
   kubectl logs -n workers deploy/workers --tail=100
   ```
2. If `workers` is up but not draining, check whether it's stuck on a
   downstream dependency (same Postgres Secret-drift failure mode as
   `WorkersListenerErrorRate` is worth ruling out first, since a stuck
   DB write per message would show up as lag, not necessarily as a
   listener error):
   ```
   kubectl exec -n api postgresql-0 -- env | grep PASSWORD
   kubectl get secret postgresql -n api -o jsonpath='{.data.password}' | base64 -d
   ```
3. Confirm the consumer group is actually attached to real partitions
   and not rebalancing endlessly:
   ```
   kubectl exec -n kafka kafka-controller-0 -- kafka-consumer-groups.sh \
     --bootstrap-server localhost:9092 --describe --group workers
   ```
4. If the group/topic itself looks wrong (missing partitions, group not
   found), check for the known topic-wipe-on-broker-restart behavior
   (ADR 0011) — a broker pod restart drops all topics on this cluster's
   ephemeral storage, which would show up here as a sudden lag spike or
   a consumer group with no assigned partitions at all:
   ```
   kubectl get pods -n kafka
   kubectl exec -n kafka kafka-controller-0 -- kafka-topics.sh \
     --bootstrap-server localhost:9092 --describe --topic work-items
   ```

## How to confirm resolution

1. Re-query the same expression against live Prometheus and confirm the
   summed lag is back under 500:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sum(kafka_consumer_fetch_manager_records_lag{job="workers"})'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c WorkersConsumerLagHigh
   ```
3. Cross-check with `kafka-consumer-groups.sh --describe` directly —
   `LAG` column back near 0 across all 3 partitions confirms it's
   actually draining, not just the Prometheus scrape catching a
   momentary dip.
