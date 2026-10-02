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

## Postscript (2026-07-31, backlog #47): re-run under real sustained traffic

Same fault, same method (`kubectl scale statefulset kafka-controller -n
kafka --replicas=0`), the one real difference from the original run:
`workload-generator` (#45) now drives continuous real traffic, so this
time the question is falsifiable rather than reasoned-from-absence.

```
$ date -u
Thu Jul 30 21:19:17 UTC 2026
$ kubectl scale statefulset kafka-controller -n kafka --replicas=0
```

selfHeal reverted the pod even faster than the original run — already
`1/1 Running` on the very first check, under a minute after the scale
command. But Finding 2 from the original run (ephemeral storage wipes
topics on every restart) turned that into a real, *ongoing* outage
anyway: `api` kept throwing real `UnknownTopicOrPartitionException`s
against the now-topicless broker for several more minutes, confirmed
directly in its logs at `21:30:17Z`/`21:30:28Z`/`21:31:00Z` — the pod
being "Ready" again did not mean the outage was over.

**The alert fired, unaided, for the first time on a re-run of this
scenario:**

```
$ curl localhost:9095/api/v2/alerts   # via in-cluster exec
ApiHighErrorRate  startsAt=2026-07-30T21:28:01.292Z  state=active
```

Elapsed from fault injection to firing: **~8m44s** — a real, sustained
non-zero error rate from continuous synthetic traffic hitting the
missing-topic condition, no manually-generated burst needed this time.
Real `ntfy.sh` push notification confirmed delivered for this firing
(same topic as the original scenario's proof). The original explanation
("traffic volume, not the rules, is why nothing fired") is now
falsified in the direction it predicted: given real sustained traffic,
the existing rule works exactly as designed.

Recovery required the same manual topic re-creation as the original run
(`kafka-topics.sh --create` for `work-items`, `work-items.DLT`,
`clinvar.ingestion.completed`) — this time confirmed to be
sufficient on its own, no pod restart needed; `api`/`workers` picked up
the recreated topics on their next metadata refresh and produce/consume
resumed within ~20s of topic creation.

**Backlog #42 reassessed, as its own dependency on this item requires:**
not closed, but downgraded. The finding that motivated it — "no alert
catches a brief Kafka outage, only downstream error-rate inference that
needs real traffic to trip" — is now half-obsolete: given #45's
permanent real traffic, `ApiHighErrorRate` *does* catch this cluster's
actual Kafka failure mode (broker restart → topic loss → sustained
downstream errors) in a real, bounded ~9 minutes, with no new alert
rule needed. What #42 would still catch that this doesn't: a Kafka
outage that resolves *before* the topics are lost or before 5 minutes
of elevated error rate accumulate (e.g. a broker restart under
non-ephemeral storage, or a network partition shorter than this
cluster's observed recovery time) — a scenario this cluster's own
ephemeral-storage design doesn't currently produce, but a real
broker/topic health alert would generalize to. Recorded as: valuable
defense-in-depth, no longer the P1 gap-filler it was scoped as —
downgraded to P2 in the backlog.

## Postscript (2026-08-15, platform#178): the AC's own alert built, and re-run live to verify it

`KafkaBrokerUnavailable` (a real `blackbox-kafka-tcp` TCP-connect probe
against the broker Service, `for: 2m`, independent of `api`/`workers`
traffic entirely — the direct answer to this scenario's "no existing
alert fired" gap) shipped in platform#178. Re-ran this same fault
injection to verify it live, exactly as #42's own AC requires:

```
$ date -u
Sat Aug 15 08:47:18 UTC 2026
$ kubectl scale statefulset kafka-controller -n kafka --replicas=0
```

**Two real, different findings from this run than either prior one:**

1. **Recovery is now fully automatic, not just pod-level.** selfHeal
   reverted the replica count in under 20s (even faster than either
   prior run), and — unlike both 2026-07-26 and 2026-07-31, which
   needed a manual `kafka-topics.sh --create` — a `kafka-provisioning`
   Job ran on its own and recreated every real topic
   (`work-items`, `clinvar.ingestion.completed`, `stock.price.tick`,
   `news.sentiment.scored`, `news.article.published`, plus aggregator's
   four changelog topics) with zero manual intervention. Proven end to
   end, not assumed: a real `POST /work-items` at `08:50:xx` returned
   `202`, and Loki confirms `workers` (KEDA-scaled 0→1 for the occasion)
   actually consumed it — `Consumed work item id=ca1dd6a6-...` at
   `08:51:20.938Z`. This project's own ephemeral-Kafka provisioning
   story has quietly gotten more resilient since the original scenario
   was written, not just faster.

2. **The alert did not fire — and the real cause is the scrape
   interval, not just the `for:` value.** Queried the raw
   `probe_success{job="blackbox-kafka-tcp"}` series directly from
   Prometheus rather than assuming: the last confirmed-`1` sample before
   the flip and the first confirmed-`1` sample after bound the real
   downtime at `08:47:50` to `08:48:50`, **~60 seconds** — an honest
   bound, not a precise duration: `/api/v1/targets` confirms this job's
   real `scrapeInterval` is **1 minute**, so the query's 5s step is
   interpolating a single real ~60s-cadence sample, not true 5s
   resolution. Comfortably under the alert's `for: 2m` either way.
   Checked *why* a shorter `for:` alone wouldn't reliably fix this: a
   sub-scrape-interval outage can land inside a single scrape gap and
   produce at most one real `0` sample, which cannot sustain any `for:`
   duration longer than roughly one evaluation cycle. This is a real,
   structural detection-resolution limit (scrape cadence vs. recovery
   speed), not a threshold-tuning miss.
   **Decision recorded, not left as an open gap**: given (a) this
   cluster's real self-heal now resolves *and* re-provisions faster
   than a human could act on a page anyway, and (b) `ApiHighErrorRate`
   already covers the slower, worse failure shape (topics genuinely
   lost, sustained error rate) per the 2026-07-31 postscript above,
   paging on a sub-minute blip that resolves itself is not worth
   tightening the scrape interval for — accepted as-is, matching this
   project's own stated "no framework for a problem you don't have yet"
   discipline. `KafkaBrokerUnavailable` stays exactly as configured: it
   would still catch a real outage lasting longer than ~2-3 real scrape
   intervals, which is its actual, intended job.

**Backlog #42 marked Done, with the gap named in the label itself
(2026-08-15)** — deliberately not a bare "Done": the AC's literal text
asked for the alert to *fire* on a real outage, and this live run's
actual result is that it did not, for the only real outage shape this
cluster currently produces. What *is* fully satisfied: the alert
exists, is independent of `api`/`workers` traffic, and was verified
live against a real repeated chaos scenario 1 rather than assumed to
work. The "reasonable window" the AC left unstated is resolved here,
after the fact, by the decision above — not redefined quietly. See
backlog.md's own #42 entry for the exact wording used there.
