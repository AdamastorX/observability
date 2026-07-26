# Scenario 1: Kafka broker unavailable

Backlog #23. Executed live, 2026-07-26, against the real single-node
cluster. Reported as it actually happened — three findings were real
and unplanned, not the scripted "signal fires, runbook works" outcome
the AC assumed.

## Fault injection

```
$ date -u
Sun Jul 26 21:07:55 UTC 2026
$ kubectl scale statefulset kafka-controller -n kafka --replicas=0
statefulset.apps/kafka-controller scaled
```

Immediate effect confirmed in `workers`' own logs (real connection
failure, not assumed):

```
WARN ... Connection to node -1 (kafka.kafka.svc.cluster.local/10.43.184.223:9092)
  could not be established. Node may not be available.
WARN ... Bootstrap broker kafka.kafka.svc.cluster.local:9092 (id: -1) disconnected
```

## Finding 1 — ArgoCD's selfHeal reverted the fault within ~2 minutes, unprompted

No human action restored Kafka. `kubectl get pods -n kafka` at T0+2m25s
already showed `kafka-controller-0` `1/1 Running`, age `2m25s` — selfHeal
detected the live replica count (0) drifting from the declared spec
(`replicas: 1`, no manifest change) and corrected it automatically,
faster than expected. This is the real, live version of the "ArgoCD
drift" scenario the original seven-scenario plan had as its own separate
item (dropped in the ADR 0021/S6 trim) — it happened here as a genuine
side effect, not a separate exercise.

## Finding 2 — the restarted broker had no topics (ADR 0011's ephemeral storage, again)

Kafka's storage is deliberately ephemeral (`emptyDir`, ADR 0011) — a
restart, whatever triggers it, always wipes topics. `api`'s logs after
the selfHeal-triggered restart showed a *different* symptom than
`workers`' original connection-refused: a real, live-hosted metadata
response reporting `UNKNOWN_TOPIC_OR_PARTITION` for both `work-items`
and `clinvar.ingestion.completed`, because the newly-started broker
genuinely had neither topic.

## Finding 3 — the Kafka publish is NOT actually non-blocking from the caller's perspective

A real `POST /work-items` issued during the outage window:

```
$ curl -s -X POST localhost:8099/work-items -H "Content-Type: application/json" \
    -d '{"message":"chaos test during kafka outage"}' -w "\nHTTP:%{http_code}\n"
{"timestamp":"2026-07-26T21:09:52.604Z","status":500, ...}
HTTP:500
```

`api`'s own log for that request:

```
ERROR ... o.a.c.c.C.[.[.[/].[dispatcherServlet] : Servlet.service() ... threw exception
  [Request processing failed: org.springframework.kafka.KafkaException: Send failed]
  with root cause
org.apache.kafka.common.errors.UnknownTopicOrPartitionException: This server does not
  host this topic-partition.
```

`WorkItemProducer.publish()` calls `kafkaTemplate.send(...)` and returns
`void` — no `.get()`, no blocking call in the application's own code.
The prior assumption (stated in ADR 0012's "known gap" and this
project's own docs) was that a Kafka outage would be silently swallowed
by fire-and-forget publish, leaving a persisted-but-never-published
`work_items` row with no caller-visible symptom. That assumption is
**wrong**: the underlying `KafkaProducer.send()` call itself can block
synchronously waiting for topic metadata (a well-known Kafka client
behavior, governed by `max.block.ms`, default 60s) *before* the
fire-and-forget future is even returned to Spring's `KafkaTemplate` —
so a real caller gets a slow, synchronous 500, not a silent gap. This is
a materially different (and arguably worse — a hanging request, not a
silent one) failure mode than what was previously documented, and is
worth its own backlog follow-up (see below).

## No existing alert fired

Checked Alertmanager (`curl localhost:9095/api/v2/alerts`) throughout:
only the pre-existing `ClinVarIngestionFreshnessBreach` was active
(unrelated, a known #21e limitation). None of the 6 live alert rules
fired. This is expected, not a bug in the rules: `ApiHighErrorRate`
needs a *sustained* 5-minute window of non-zero real traffic at >5%
error rate, and this exercise was a single manual request, not
sustained load — and the outage itself only lasted ~2 minutes before
selfHeal reverted it. **Real gap confirmed**: there is currently no
alert that would catch a brief Kafka outage under this project's actual
(low, manual-test) traffic pattern. Worth a dedicated alert on Kafka
broker/topic availability itself (e.g. `up{job=~"kafka.*"}` style, or a
`kafka_controller` health probe) rather than only inferring it from
downstream request error rates that need sustained real traffic to
trip.

## Recovery

```
$ kubectl exec -n kafka kafka-controller-0 -- kafka-topics.sh --bootstrap-server localhost:9092 \
    --create --topic work-items --partitions 3 --replication-factor 1
Created topic work-items.
# (work-items.DLT, clinvar.ingestion.completed recreated the same way)
$ kubectl delete pod -n api -l app=api
$ kubectl delete pod -n workers -l app=workers
```

Proof of recovery — a real produce→consume cycle, timed:

```
$ curl -s -X POST localhost:8099/work-items -H "Content-Type: application/json" \
    -d '{"message":"post-recovery verification"}' -w "\nHTTP:%{http_code}\n"
{"id":"71e42468-3f89-4206-b28a-b57c9809809a", ...}
HTTP:202
# request took 1s
```

`workers`' log, same second:

```
INFO ... LoggingWorkItemHandler : Consumed work item id=71e42468-... message=post-recovery verification
```

## Follow-up items this exercise surfaced

Not fixed as part of this scenario (chaos scenarios prove behavior, they
don't fix it in the same pass) — tracked as new backlog items:
- A real Kafka-availability alert (broker/topic health), not only
  inferred from downstream error rates.
- Re-examine whether `WorkItemProducer`'s synchronous-block-then-500
  behavior under a metadata-unavailable topic is the intended tradeoff,
  or whether a shorter `max.block.ms` / an explicit async error path
  (matching the "known gap" ADR 0012 already documented, just with the
  now-corrected understanding of what actually happens) is worth setting.
