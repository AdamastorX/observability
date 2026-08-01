# Scenario 3: consumer-group lag (`workers` falling behind `work-items`)

Backlog #23. Executed live, 2026-08-01, against the real single-node
cluster. Took two attempts, like scenario 2 — the first self-healed
before it could be observed. The scenario's own intended finding (does
`WorkersConsumerLagHigh` fire) turned out to be the smaller story; a
real, unanticipated broker instability along the way is the bigger one.

## Prep: the default rate is too slow to test in a reasonable window

`WorkersConsumerLagHigh` fires on `sum(kafka_consumer_fetch_manager_records_lag{job="workers"}) > 500`
sustained `10m`. At `workload-generator`'s (#45) default `target_rps: 0.5`
(≈0.2 work-item writes/s), reaching 500 un-consumed records takes
~40 minutes on its own, before the 10-minute sustained window even
starts. `target_rps` was temporarily bumped to `10` via a real,
committed change to `kubernetes/workload-generator/configmap.yaml`
(platform#72) — the same real-traffic mechanism #45 already documents
("a single configurable value... no redeploy"), not a live `kubectl`
edit: a live edit to this ConfigMap was tried first and reverted by
ArgoCD's selfHeal before ever reaching the running pod, the same
nested-app-of-apps lesson this project has hit repeatedly. Reverted via
platform#74 partway through this scenario — see the finding below for
why sooner than planned.

## Attempt 1 — naive `kubectl scale --replicas=0`, self-healed instantly

```
$ date -u
Sat Aug  1 09:21:11 UTC 2026
$ kubectl scale deployment workers -n workers --replicas=0
```

Lag stayed at `0` for the entire 16-minute observation window that
followed — not because nothing happened, but because nothing *was*
happening: `workers`' Deployment was back at `1/1 Running`/`Synced`
essentially immediately, the same selfHeal-reverts-a-live-scale pattern
scenarios 1 and 2 already documented. No real outage window ever
existed on this attempt.

## Attempt 2 — real, git-committed sync pause

Same fix already proven for `postgresql` (platform#53/#66): removed
`spec.syncPolicy.automated` from `argocd/apps/workers.yaml`
(platform#73), merged, `root` given its own `refresh=hard` to notice
the tracked-file change (the same "refresh the parent, not the child"
finding scenario 2 recorded).

```
$ date -u
Sat Aug  1 09:41:55 UTC 2026
$ kubectl scale deployment workers -n workers --replicas=0
$ kubectl get pods -n workers
No resources found in workers namespace.
```

Confirmed genuinely down this time — no revert.

## Finding 1 — the lag metric itself goes dark exactly when the consumer fully stops

Checked directly, not assumed: with `workers` at zero replicas,
Prometheus's own `/api/v1/targets` shows **zero active targets for
`job="workers"`** (the pod-role service-discovery target simply has
nothing to discover) — and `kafka_consumer_fetch_manager_records_lag`
returns an **empty result set**, not a large number, not `0`, nothing
at all. This is a real, structural gap: the metric
`WorkersConsumerLagHigh` alerts on is *self-reported by the consumer
process itself* (a Micrometer `KafkaClientMetrics` binder inside
`workers`' own JVM, #21a) — so the one failure mode this alert's own
`description` field already names as a thing to check ("check if the
pod is even running/consuming at all") is precisely the one case where
the alert's own signal cannot exist to fire on. A slow-but-connected
consumer produces a real, growing lag number the rule can catch; a
fully-stopped consumer produces silence, indistinguishable at the
Prometheus level from "nothing to report because everything is fine."
**`WorkersConsumerLagHigh` never fired at any point in this scenario** —
confirmed via `curl localhost:9095/api/v2/alerts` returning no results
for it throughout, consistent with this finding rather than a rule bug.

## Finding 2 — real, unanticipated: the broker itself became unstable under the accumulating backlog

Not part of this scenario's original plan. Checking cluster health a
few minutes into the outage:

```
$ kubectl get pods -n kafka
NAME                 READY   STATUS             RESTARTS   AGE
kafka-controller-0   0/1     CrashLoopBackOff    48         36h
$ kubectl describe pod kafka-controller-0 -n kafka
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      Sat, 01 Aug 2026 10:42:54 +0100   (09:42:54 UTC)
      Finished:     Sat, 01 Aug 2026 10:48:10 +0100   (09:47:10 UTC)
```

That crash-and-restart window (09:42:54–09:47:10 UTC) sits entirely
inside this scenario's own outage window (09:41:55–09:48:56 UTC) — a
real, timestamp-aligned OOMKill, not a coincidence from unrelated
cluster activity. Kafka's container limit is `768Mi`
(`KAFKA_HEAP_OPTS: -XX:InitialRAMPercentage=75 -XX:MaxRAMPercentage=75`,
≈576Mi heap), leaving a real but narrow ~192Mi for everything the JVM
needs outside the heap — including the OS page cache Kafka leans on
heavily for reading/writing log segments. The plausible mechanism: with
`workers` not consuming and the generator still producing at the
temporarily-elevated rate, unconsumed messages accumulate in the
broker's own log segments faster than usual, and that accumulation
pressures memory tight enough to tip the container over its limit.

**Stated honestly, not overclaimed**: this specific run cannot cleanly
separate "backlog accumulation caused the pressure" from "the
temporarily-elevated generator rate alone was enough" — both were true
at the same time here. What's real and directly evidenced: Kafka's
restart count moved from 47 (pre-existing, accumulated over this pod's
36h uptime across every earlier chaos scenario and test this session)
to 48 during this exact window, with a matching `OOMKilled` reason and
timestamps inside the fault window. A clean re-run at the *unmodified*
default `target_rps: 0.5` — isolating "consumer stopped, normal
traffic continues" without any rate acceleration — would be needed to
attribute this cleanly to backlog size alone rather than raw request
rate. Tracked as new backlog #75 rather than re-run in the same
session that just destabilized the broker once already.

## Recovery — real, prioritized over completing the original test plan

Given a real broker instability in progress, `workers` was restored
early (7m01s into the outage, before the originally-planned 10-minute
sustained-lag window completed) rather than continuing to let the
backlog grow:

```
$ date -u
Sat Aug  1 09:48:56 UTC 2026
$ kubectl scale deployment workers -n workers --replicas=1
```

Real, immediate drain observed in `workers`' own logs — a genuine
catch-up burst, not steady-state pace:

```
2026-08-01T09:49:56.124Z ... Consumed work item id=9cd502ec-...
2026-08-01T09:49:56.159Z ... Consumed work item id=5e80fa90-...
2026-08-01T09:49:56.226Z ... Consumed work item id=c43a686a-...
2026-08-01T09:49:56.256Z ... Consumed work item id=a6e43003-...
2026-08-01T09:49:56.357Z ... Consumed work item id=702b7f50-...
2026-08-01T09:49:56.366Z ... Consumed work item id=a39da49e-...
```

Six real items consumed inside a 250ms window is not this generator's
normal request pace at any rate setting — direct evidence of a real
backlog being drained, not simulated. Kafka's own restart count held
steady at 48 (no further OOM) from the moment `workers` came back up
through the rest of this session — consistent with the "consumer
resumes, pressure relieves" side of the hypothesis in Finding 2, though
not proof on its own given the generator rate revert (below) landed in
the same short window.

`kafka_consumer_fetch_manager_records_lag` reappeared once Prometheus's
next scrape found the recreated pod, confirming `0` shortly after
(consistent with Finding 1: the metric's presence itself, not just its
value, is a signal — its absence *is* the "consumer completely down"
state, its return is part of what "recovered" looks like here).

## Cleanup

- `workload-generator`'s rate reverted to the real default (`0.5`) via
  platform#74, confirmed live in the running pod's mounted config.
- `workers`' Application sync pause reverted via platform#75 (a real
  `git revert` of platform#73, same precedent as scenario 2's #53/#54
  pair); `root` refreshed, confirmed `automated.selfHeal: true` live
  again.
- Kafka topics survived this restart (unlike scenario 1's broker
  restart) — this was a container-level OOM-restart within the same
  Pod, not a Pod reschedule, so the `emptyDir` backing Kafka's storage
  (ADR 0011) was never actually torn down. Confirmed via
  `kafka-topics.sh --list` showing all four topics present after
  recovery.
- No data loss observed: every real work-item produced during the
  outage was consumed during the drain (workers' logs show continuous,
  gap-free `Consumed work item` lines from the recovery burst through
  steady-state); no separate reconciliation was needed the way #54's
  ClinVar job-tracking needed for its own crash scenario, because
  Kafka's own offset-commit model already gives `workers` at-least-once
  delivery across the restart for free.

## Follow-up items this exercise surfaced

- **New backlog #75**: Kafka's `768Mi` memory limit is real, current,
  and now has one real (if not perfectly isolated) OOM event against
  it under a real accumulating backlog — re-examine whether it has
  enough headroom, ideally via a clean re-run of this exact scenario at
  the *unmodified* default traffic rate to isolate the true cause
  before deciding on a fix.
- **New backlog #76**: `WorkersConsumerLagHigh` cannot detect "the
  consumer is completely stopped" (Finding 1) — the same shape of gap
  #42 already tracks for Kafka's own broker/topic availability. A
  companion alert on `up{job="workers"} == 0`-style absence (or
  `absent(kafka_consumer_fetch_manager_records_lag{job="workers"})`)
  would close it; the existing rule's `description` already tells a
  human to check this by hand, but nothing alerts on it automatically
  today.
- Separately, unrelated to this scenario but found while checking
  overall cluster health during recovery: `postgresql-backup` and
  `clinvar-postgresql-backup`'s ArgoCD Applications show `Degraded`
  health from a real failed CronJob run ~8 hours before this scenario
  started (`postgresql-backup-29759160`, `Failed 0/1`) — the pod was
  already garbage-collected by the time this was noticed, so the
  failure's root cause could not be retrieved this session. Not
  investigated further here (out of scope for this scenario); worth a
  human checking the next scheduled run's outcome.
