# Scenario 2: PostgreSQL unavailable

Backlog #23. Executed live, 2026-07-27, against the real single-node
cluster. Took two attempts — the first attempt's fault self-healed
before it could be observed, which became its own real finding.

## Attempt 1 — naive `kubectl scale --replicas=0`, self-healed too fast

```
$ kubectl scale statefulset postgresql -n api --replicas=0
```

`postgresql-0` was back `1/1 Running` within **16 seconds** — ArgoCD's
selfHeal reverted the drift before any request could be issued against
a genuinely-down database. Same pattern chaos scenario 1 hit against
Kafka, faster this time.

**Attempted to pause it live**, to get a real observation window:

```
$ kubectl patch application postgresql -n argocd --type merge \
    -p '{"spec":{"syncPolicy":{"automated":null}}}'
```

This did not stick — checking `spec.syncPolicy` moments later still
showed `automated.selfHeal: true`. **Finding: nested app-of-apps
self-healing defeats a live pause.** The `root` Application (the
GitOps entrypoint, ADR 0003) manages every child `Application` object
under `argocd/apps/` — including `postgresql` itself — as one of its
own tracked resources. Patching `postgresql`'s live spec directly is
exactly the kind of drift `root`'s own `selfHeal` exists to revert, so
`root` reverted it just as fast as `postgresql` reverts drift on the
database StatefulSet it manages. There is no live, temporary way to
pause a single component's sync on this setup without either pausing
`root` itself (a much bigger blast radius — the whole cluster's GitOps
loop) or making a real, committed git change.

## PVC-full sub-scenario: not safely testable on this cluster

Checked before attempting: `local-path`'s "2Gi" PVC request is purely
nominal.

```
$ kubectl exec -n api postgresql-0 -- df -h /bitnami/postgresql
Filesystem      Size  Used Avail Use%
/dev/sda2       233G   48G  173G  22%
```

The PVC has no real quota — the underlying mount is the node's actual
root filesystem, shared by every other pod/PVC on the single node.
Genuinely filling it to test "PVC full" would mean filling a real 173GB
of shared node disk, which risks destabilizing every other component on
the cluster, not just Postgres — an unacceptable blast radius for a
chaos exercise. **This sub-scenario's own premise doesn't hold on this
infrastructure**: `local-path` never enforces the PVC size a manifest
requests, so "the PVC fills up" isn't a distinct failure mode from "the
node's real disk fills up," and the latter is out of scope for a safe,
reversible test. Reported as a real finding, not attempted.

## Attempt 2 — real, git-committed sync pause

```
$ git checkout -b chaos-scenario-2-pause-postgres-sync
# removed spec.syncPolicy.automated from argocd/apps/postgresql.yaml
$ git commit ... && git push
# PR opened, reviewed, merged (platform#53)
```

Even after merge, the live `Application` object still showed
`automated.selfHeal: true` for a noticeable window — `root` needed its
own explicit refresh (`argocd.argoproj.io/refresh=hard` on `root`
itself, not just `postgresql`) before it picked up the change to the
tracked `postgresql.yaml` manifest and reconciled the child spec. A
direct `kubectl patch --type json` on the live object (removing
`/spec/syncPolicy/automated`) was needed once to unstick it. **Second
finding: refreshing a child Application doesn't refresh `root`'s own
awareness of changes to that child's own manifest file — refresh the
parent that tracks the file, not the child whose live object you're
diffing.**

With `automated` genuinely gone:

```
$ date -u
Mon Jul 27 18:06:51 UTC 2026
$ kubectl scale statefulset postgresql -n api --replicas=0
$ kubectl get pods -n api
postgresql-0   0/0   (gone, no revert)
```

Confirmed: `postgresql` StatefulSet at `0/0`, no selfHeal reverting it
this time.

## Real impact, sustained

A single request during the outage:

```
$ curl -X POST localhost:8099/work-items -d '{"message":"..."}' -w "%{http_code}" -m 20
(timed out, HTTP:000)
```

A repeat, with a longer timeout:

```
$ curl -X POST localhost:8099/work-items -d '{"message":"..."}' -w "HTTP:%{http_code} TIME:%{time_total}s"
HTTP:500 TIME:30.09s
```

**Finding: a real, measured ~30-second synchronous hang before failure**
— matching HikariCP's default connection-acquisition timeout — the same
"synchronous dependency blocks the HTTP thread for tens of seconds"
pattern chaos scenario 1 already found on the Kafka side. Two
independent integration points (Kafka producer, Postgres connection
pool) share this shape: a downstream outage doesn't fail fast, it hangs
the caller for tens of seconds first.

**Finding: the readiness probe never detected the outage.**
`/actuator/health/readiness` (what Kubernetes' own readiness probe
checks) kept returning `{"status":"UP"}` throughout — Kubernetes never
stopped routing traffic to a pod that could not actually serve a
DB-backed request. The full, ungrouped `/actuator/health` endpoint (which
does include the DataSource indicator) correctly hung/failed, confirming
the DB check itself works — it's just excluded from the readiness
*group* specifically. This is very likely Spring Boot's own intentional
design (readiness deliberately excludes downstream-dependency health so
one DB blip doesn't pull every replica out of rotation simultaneously,
worse than degraded single-node service) rather than a bug — but the
real, concrete consequence is that nothing at the Kubernetes level
signals this degradation; the entire burden falls on `ApiHighErrorRate`
alerting on real, sustained traffic.

## Proof the alert actually fires — this time, for real

Given a genuine multi-minute outage window (attempt 1 never got one),
sustained real failing traffic was generated for ~6 minutes:

```
$ date -u  # burst start
Mon Jul 27 18:21:49 UTC 2026
# (repeated real POST /work-items every ~8s for 330s)
$ date -u  # burst end
Mon Jul 27 18:27:48 UTC 2026
```

Real result — the alert fired, for the first time ever on this cluster:

```
$ curl localhost:9095/api/v2/alerts
ApiHighErrorRate active 2026-07-27T18:27:01.292Z
```

And the real notification arrived at the live `ntfy.sh` topic:

```
$ curl "https://ntfy.sh/adamastorx-alerts-.../json?poll=1&since=30m"
{"message":"{\"receiver\":\"ntfy\",\"status\":\"firing\",
  \"alerts\":[{\"labels\":{\"alertname\":\"ApiHighErrorRate\", ...
  \"notification_reason\":\"first notification\", ...}"}
```

Full proof loop closed: fault injected → real degradation measured →
real alert fired → real push notification delivered.

## Recovery

```
$ kubectl scale statefulset postgresql -n api --replicas=1
$ kubectl get pods -n api
postgresql-0   1/1   Running   0   28s
$ curl -X POST localhost:8099/work-items -d '{"message":"post-recovery"}' -w "HTTP:%{http_code}"
HTTP:202   # 0s, immediate
```

`ApiHighErrorRate` stayed `active` for several more minutes after
recovery — expected, not a bug: its `rate(...[5m])` window needs the
failed requests to actually age out before the ratio drops back under
threshold. Confirmed cleared on a later check.

Sync pause reverted via a second real PR (platform#54) once the
scenario was done; `root` needed its own explicit refresh again to pick
up the reversion, consistent with the earlier finding.

## Follow-up items this exercise surfaced

- `local-path`'s unenforced PVC quota (confirmed, not new — already
  known) means "PVC full" as a distinct, safely-testable scenario
  doesn't really exist on this infrastructure; #23's own scenario list
  should be read as "Postgres unavailable" only for this cluster, not
  "and separately, PVC full" as originally scoped.
- Nested app-of-apps self-healing (root reverts child Application spec
  edits) is worth a documented gotcha for future chaos exercises on
  this GitOps setup — see `docs/SESSION_STATE.md`.
- The readiness-probe-excludes-downstream-dependencies behavior is
  worth a stated, deliberate decision note (not a bug fix) — whether
  this project accepts Boot's default split as-is, given the real
  consequence measured here (a ~30s hang, no probe signal, alerting is
  the only real-time detection).

## Postscript (2026-07-31, backlog #47): re-run under real sustained traffic

Same fault, method proven necessary the first time around: a naive
`kubectl scale statefulset postgresql -n api --replicas=0` still
self-heals in under a minute with `workload-generator` (#45) now
running too (confirmed live, ~45s this time), so the same sync-pause
fix from Attempt 2 above was reapplied — `automated` removed from
`argocd/apps/postgresql.yaml` via a real, git-committed, reviewed PR
(platform#66), reverted via a second PR (platform#67) once the scenario
was done, both following the exact precedent this file already set.

```
$ date -u
Fri Jul 31 02:38:48 UTC 2026
$ kubectl scale statefulset postgresql -n api --replicas=0
```

Confirmed genuinely down this time (no revert): a real request during
the outage reproduced the original ~30s HikariCP-timeout hang exactly:

```
$ curl -X POST https://api.local.adamastorx.test/work-items -d '...' \
    -w "HTTP:%{http_code} TIME:%{time_total}s"
HTTP:500 TIME:30.082733s
```

**The alert fired, unaided, this time — no manually-generated 6-minute
burst needed:**

```
ApiHighErrorRate  startsAt=2026-07-31T02:47:01.292Z  state=active
```

Elapsed from fault injection to firing: **~8m13s**, from real,
continuous synthetic traffic hitting the down database — closely
matching chaos scenario 1's re-run (~8m44s) on the same rule. Real
`ntfy.sh` push notification confirmed delivered. The original
attempt's "the rule needs sustained real traffic, not real traffic
generated by hand" explanation is now proven, not just reasoned.

Recovery: `kubectl scale statefulset postgresql -n api --replicas=1`,
ready within ~10s, a real `POST /work-items` immediately after returned
`202` in 0.03s — full recovery confirmed, not assumed. The sync-pause
was reverted afterward (platform#67) and confirmed live (`root`
refreshed, `postgresql` Application back to `automated.selfHeal: true`,
`Healthy`); the resulting brief `OutOfSync` on the chart's Secret
object is the same already-documented platform#34/#36
`prune: false` cosmetic drift, not a new issue.

**Backlog #42 reassessed** alongside chaos scenario 1's identical
finding: `ApiHighErrorRate` now catches both this project's real
downstream-outage shapes (Kafka topic loss, Postgres unavailability) in
a real, bounded ~8-9 minutes under permanent real traffic, with no new
Kafka-specific alert needed for *this* failure mode. #42 keeps value
only for a Kafka failure that resolves faster than that — recorded as
downgraded (P1 → P2), not closed. No equivalent item was ever proposed
for Postgres specifically, and this result doesn't create the need for
one.
