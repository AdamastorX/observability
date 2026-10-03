# ContainerCrashLoopOrOOMKilled

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1) or ((kube_pod_container_status_last_terminated_exitcode == 137) and (increase(kube_pod_container_status_restarts_total[15m]) > 0))
```

`for: 2m`, `severity: critical`.

Backlog #125. Two real, terminal-state signals in one alert,
deliberately not two separate rules: a pod stuck in `CrashLoopBackOff`
right now (the real #35 shape — a rollout stuck restarting for 95
minutes while the old pod kept serving, nothing alerting), and a
container that both was OOM-killed and restarted within the last 15
minutes.

**Exit code, not the reason string — found live, not assumed.** The
obvious expression would key on
`kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}`.
Confirmed live before writing this that this cluster's kubelet does
**not** reliably report that string: `mimir`'s own container — real,
confirmed OOM (`container_memory_working_set_bytes` cycling against its
limit, backlog #135) — reports
`kube_pod_container_status_last_terminated_reason` as the generic
`"Error"`, never `"OOMKilled"`, even though
`kube_pod_container_status_last_terminated_exitcode` correctly shows
`137` for the same container at the same time. An alert keyed on the
reason string alone would never have fired on this cluster's own real,
already-known OOM pattern. The numeric exit code doesn't have that
gap — `137` is unambiguous (`128 + SIGKILL`) regardless of what string
the kubelet chose to report.

**Restart-recency gate on the OOM arm only — backlog #143.**
`kube_pod_container_status_last_terminated_exitcode` is a gauge: once a
container dies with `137` it holds that value forever, not just at the
moment of death, until its *next* termination overwrites it. Without a
recency bound, one real OOM paged `severity: critical` indefinitely,
long after the pod recovered — verified live 2026-08-22/23 as the
highest signal-to-noise cost in the whole alert set (two of three
currently-firing subjects were stale noise, see below). The
`and increase(...restarts_total[15m]) > 0` clause means this arm now
only fires in a tight window right after a real restart, and is
expected to **clear between restarts and re-fire on whatever cadence
the subject actually restarts at** — for a chronic subject like
`mimir` that means clearing and re-firing every cycle, not staying lit
continuously. That's a deliberate consequence of the fix, not a
flapping bug: `CrashLoopBackOff` already covers "stuck restarting right
now," and `WorkloadRestartingFrequently` already covers "restarting too
often over a longer window" — this arm's only remaining job is "did a
real OOM kill *just* happen." The `CrashLoopBackOff` arm is untouched
and has no recency gate (its own underlying metric,
`kube_pod_container_status_waiting_reason`, is already live/current by
construction — a waiting-reason gauge reflects the pod's state *right
now*, not a historical one-off, so it doesn't have the stale-gauge
problem the OOM arm had).

## What it means in practice

The pod named in the alert either can't stay up (CrashLoopBackOff) or
its container was just killed for real memory pressure (exit 137).
Both are real, actionable failure states — this project has hit both
shapes before and both times the pod stayed `Healthy`/`Running` from
Kubernetes' own perspective with nothing watching (#85's RocksDB crash,
#86's poisoned Kafka producer, #35's 95-minute stuck rollout).

**This alert is expected to fire immediately on first deploy, for
three already-known, already-triaged real subjects** — recorded here
so the first on-call read of this alert isn't a fresh investigation:

- **`mimir`**: chronic, accepted, already tracked (backlog #135). Its
  memory limit is deliberately *not* being raised — #135's own decision
  is a conditional decommission, not further investment in a component
  under evaluation for removal. Confirm the real rate hasn't
  meaningfully changed from its known pattern (see
  `WorkloadRestartingFrequently.md`'s own check) before treating a
  firing as new.
- **`prometheus-server`** and **`beyla`**: a real, single, correlated
  OOM event on 2026-08-18 (both pods' `lastState.terminated.finishedAt`
  within minutes of each other). Consistent with backlog #131's
  already-documented finding that real node-wide memory pressure can
  kill workloads independent of their own container limits, not a new
  per-service bug in either. Since backlog #143's restart-recency gate
  shipped, a real recurrence pages again on its own (the gate only
  silences *stale* exit-code-137 gauges, not new ones) — so if this
  fires for either subject again, don't assume it's an echo of the
  2026-08-18 event: check the real current restart count/age
  (`kubectl get pod ... `, `lastState.terminated.finishedAt`) before
  treating it as the same known one-off. A real recurrence changes the
  read from "one-off correlated event" to "a real, recurring pattern,"
  and their own memory limits are worth a real measurement pass at that
  point (the same #84 method mimir's own history already used), not
  before.

Any subject *other than* these three is a genuinely new finding — treat
it as a real incident, not as one of the three above.

## First response

1. Identify exactly which state fired and the real detail behind it:
   ```
   kubectl describe pod {{ $labels.pod }} -n {{ $labels.namespace }}
   ```
2. If `CrashLoopBackOff`: read the container's own previous logs for
   the real crash cause, don't guess from the restart count:
   ```
   kubectl logs {{ $labels.pod }} -n {{ $labels.namespace }} --previous --tail=200
   ```
3. If exit code `137`: confirm real memory usage against the
   container's own limit, and check for a correlated cluster-wide event
   (matching backlog #131's own finding) before assuming a
   single-service leak:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     "query=container_memory_working_set_bytes{namespace=\"{{ \$labels.namespace }}\",pod=\"{{ \$labels.pod }}\"}"
   kubectl top node
   ```
4. If the pod is one of the three known subjects above, confirm it
   still matches that known shape (see "What it means in practice")
   before escalating as new.

## How to confirm resolution

1. Confirm the pod is no longer in either terminal state:
   ```
   kubectl get pod {{ $labels.pod }} -n {{ $labels.namespace }}
   ```
   should read `Running`, not `CrashLoopBackOff`, with a stable (not
   climbing) restart count.
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ContainerCrashLoopOrOOMKilled
   ```
   Note: since backlog #143's restart-recency gate, the OOM arm is
   *expected* to clear on its own within 15 minutes of any restart, for
   every subject, not just `mimir` — that alone is not evidence of a
   fix. For `mimir` specifically, expect this alert to keep clearing
   and re-firing on its own known chronic cadence (backlog #135) —
   that is the accepted, tracked behavior, not a resolution to chase to
   zero. For any other subject, real resolution means the pod's own
   restart count has gone stable (not climbing) *and* stayed that way
   past the next 15-minute window, not just that the alert happens to
   be quiet at the moment you checked.
