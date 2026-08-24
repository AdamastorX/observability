# TelemetryBackendDown

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
up{job=~"loki|tempo|pyroscope"} == 0
```

`for: 5m`, `severity: warning`.

Prometheus itself can no longer scrape one of the observability stack's
own telemetry backends — `loki`, `tempo`, or `pyroscope` — for 5
straight minutes. This is not one of this project's application
services failing; it's the *observability tooling itself* going dark,
which is the exact blind spot backlog #144 (ADR 0044 section 3, the
2026-08-21 staff-engineer audit) found and named as the worst
combination on this cluster: a component that can silently break
another SLO (backlog #94's 30-day retention story) while being the one
component with no health signal of its own.

Three jobs share this one alert (`loki`, `tempo`, `pyroscope`) — the
same shape [`BlackboxProbeFailing`](BlackboxProbeFailing.md) already
uses for its three blackbox jobs. The failing backend is a real label
on the firing series (`job`), not something to guess from the alert
name alone — `job` and the namespace it runs in are identical strings
for all three (`job="loki"` runs in the `loki` namespace, etc.).

`up`, not `absent(...)`: all three scrape jobs are `static_configs`
(each backend has a stable ClusterIP Service), so the target address
always exists in Prometheus's own config regardless of whether the pod
answers — `up` always has a series and reads `0` the moment a scrape
actually fails, the standard target-down primitive for a static target.
This is a different shape from `WorkersConsumerMissing`/
`AggregatorConsumerMissing`, which key off pod-role service discovery
finding zero targets (a metric going dark entirely) rather than a known
static target failing to answer.

**Deliberately does not cover Mimir.** Backlog #135 (Mimir decommission
trigger) is still an open owner decision as of this alert shipping — a
Prometheus-to-Mimir remote-write-failure alert is a materially
different signal (Prometheus's own outbound push failing, not a
target Prometheus scrapes) and would be throwaway work if #135
resolves to "decommission." See backlog #144's own scope split. If
Mimir is kept, its own remote-write-failure arm is a separate,
follow-up alert/runbook — not this one.

## What it means in practice

- **`job="loki"`**: no logs are being ingested or queryable via Grafana
  until Loki is back — anything relying on live log correlation (an
  active incident, a runbook's own "check the logs" step) is flying
  blind in the meantime.
- **`job="tempo"`**: no new traces are being ingested — span-based
  debugging and the Tempo/Grafana correlation with logs/metrics is
  unavailable until it's back.
- **`job="pyroscope"`**: no new continuous-profiling data is being
  captured — the #35-style "which code actually burned that CPU"
  investigation this component exists for is unavailable until it's
  back.

None of these three block the live application path itself (`api`,
`workers`, the M13 pipeline, `clinvar-service`, `watchlist-service` all
keep serving real traffic normally) — this is why the alert is
`severity: warning`, not `critical`, the same "our own infra is
misbehaving, not our product" reasoning
[`WorkloadRestartingFrequently`](WorkloadRestartingFrequently.md) uses.
But losing one of these silently, with no alert, is exactly the
backlog #144 gap: whatever window it's down for is a real, permanent
gap in that signal's history (Loki has 72h retention, Tempo 72h,
Pyroscope 72h compactor window — none of these backfill).

## First response

1. Identify which backend failed from the alert's own real `job` label,
   then check that backend's own pod directly (its namespace has the
   same name as the `job` label):
   ```
   kubectl get pods -n <job>
   kubectl describe pod -n <job> <pod-name>
   kubectl logs -n <job> <pod-name>
   ```
2. Check the pod's own resource pressure first — all three
   (`argocd/apps/loki.yaml`, `argocd/apps/tempo.yaml`,
   `argocd/apps/pyroscope.yaml`) run a single StatefulSet replica with
   explicit CPU/memory limits; an OOM kill or `CrashLoopBackOff` here
   would also be independently caught by
   [`ContainerCrashLoopOrOOMKilled`](ContainerCrashLoopOrOOMKilled.md) —
   check whether that alert is also firing for the same pod, which
   would confirm the cause rather than leave it to infer:
   ```
   kubectl top pod -n <job>
   ```
3. If the pod looks healthy (`Running`, `Ready`) but the scrape still
   fails, check the real Service and the `CiliumNetworkPolicy` egress
   path from `prometheus-server` — the scrape targets are
   `loki.loki.svc.cluster.local:3100`,
   `tempo.tempo.svc.cluster.local:3200`, and
   `pyroscope.pyroscope.svc.cluster.local:4040`
   (`kubernetes/prometheus-network-policies/prometheus-server.yaml` has
   the explicit egress rule for each):
   ```
   kubectl get svc -n <job>
   kubectl get --raw /api/v1/namespaces/<job>/services/<job>:<port>/proxy/metrics
   ```
   A working direct API-server-proxy fetch but a still-failing scrape
   points at the Cilium policy rather than the backend itself.
4. Check the PVC backing the failing backend isn't the real cause — all
   three use `local-path` with no enforced quota (backlog #21d);
   [`NodeDiskSpaceLow`](NodeDiskSpaceLow.md)/
   [`NodeDiskSpaceCritical`](NodeDiskSpaceCritical.md) firing at the
   same time would point at the node's disk, not the backend's own
   config:
   ```
   kubectl get pvc -n <job>
   ```
5. Check ArgoCD's own sync state for the Application — a stuck or
   reverted sync of `argocd/apps/<job>.yaml` would also produce this
   symptom, and would independently be caught by
   [`ArgoCDAppOutOfSync`](ArgoCDAppOutOfSync.md) after 2h:
   ```
   kubectl get application <job> -n argocd
   ```

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=up{job=~"loki|tempo|pyroscope"}'
   ```
2. Confirm the backend's own `<name>_build_info` series is present
   again (the same live check this alert's own scrape config was
   verified with before shipping):
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=count(loki_build_info)'
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=count(tempo_build_info)'
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=count(pyroscope_build_info)'
   ```
3. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c TelemetryBackendDown
   ```
