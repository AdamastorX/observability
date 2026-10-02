# WorkloadRestartingFrequently

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
increase(kube_pod_container_status_restarts_total[1h]) > 3
```

`for: 5m`, `severity: warning`.

Backlog #125: `kube-state-metrics` has exported
`kube_pod_container_status_restarts_total` since backlog #92
(2026-08-07) — scraped and never alerted on until now. The exact class
of gap #85/#86 each hit once and fixed with a bespoke per-service
liveness indicator (correct for those two services, zero coverage for
the other ~25 workloads in this cluster). This alert is the general
answer.

**Threshold picked against this cluster's real observed history, not a
round number.** `mimir`'s own known, chronic OOM loop (backlog #135,
accepted and tracked, not investigated per-firing) runs at roughly 7
restarts/24h (~0.3/h), confirmed live via
`increase(kube_pod_container_status_restarts_total{namespace="mimir"}[24h])`.
Real crash-loop bursts this project has actually hit run far hotter:
backlog #122's `api` OOM (70+ restarts over 41h, ~1.7/h) and backlog
#140's `market-data-ingestor` burst (43 restarts over ~2h, ~21/h). `> 3
in 1h` sits comfortably above mimir's steady background noise and
comfortably below both real incident shapes — it will not fire on
mimir's ordinary chronic pattern, and would have caught both #122 and
#140 within the hour, not days later via an operations review.

## What it means in practice

A specific pod is restarting unusually fast right now. This is a
burst-detector, not a terminal-state detector — it fires regardless of
*why* the container is dying (OOM, a real crash, a bad liveness probe,
a stuck init container), which is exactly the point: it catches
classes of crash loop no bespoke per-service indicator was written for.
`ContainerCrashLoopOrOOMKilled` is the companion alert for the two
specific terminal states (CrashLoopBackOff, a real OOM kill) — the two
often fire together, but not always (a probe-flapping restart loop
trips this one without necessarily being in `CrashLoopBackOff` at
scrape time).

## First response

1. Identify the real terminated reason and exit code directly — don't
   guess from the restart count alone:
   ```
   kubectl describe pod {{ $labels.pod }} -n {{ $labels.namespace }}
   ```
   or, faster, the exact fields this alert's own companion checks:
   ```
   kubectl get pod {{ $labels.pod }} -n {{ $labels.namespace }} \
     -o jsonpath='{.status.containerStatuses[0].lastState.terminated}'
   ```
2. If it's `mimir`, this may be the already-known, already-accepted
   chronic OOM pattern (backlog #135) — check whether the real rate
   over the last 24h is still in its known ~7/24h range or has
   genuinely spiked higher before treating it as a new incident:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=increase(kube_pod_container_status_restarts_total{namespace="mimir"}[24h])'
   ```
   Do not raise its memory limit reactively — backlog #135's own
   decision is to not invest further in Mimir (conditional
   decommission, not defended indefinitely); a real, stated
   reason, not silently ignored.
3. For any other workload, check real memory usage against its
   container limit before assuming a code bug:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     "query=container_memory_working_set_bytes{namespace=\"{{ \$labels.namespace }}\",pod=\"{{ \$labels.pod }}\"}"
   ```
4. Check for a correlated, cluster-wide event rather than assuming a
   single workload's own bug — backlog #131 already found real
   node-wide memory pressure can kill workloads independent of their
   own container limits:
   ```
   kubectl top node
   free -h
   ```

## How to confirm resolution

1. Confirm the rate has dropped back below threshold:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     "query=increase(kube_pod_container_status_restarts_total{namespace=\"{{ \$labels.namespace }}\",pod=\"{{ \$labels.pod }}\"}[1h])"
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c WorkloadRestartingFrequently
   ```
