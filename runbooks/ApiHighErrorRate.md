# ApiHighErrorRate

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(
  sum(rate(http_server_requests_seconds_count{job="api", outcome="SERVER_ERROR", uri!~"/actuator.*"}[5m]))
  /
  sum(rate(http_server_requests_seconds_count{job="api", uri!~"/actuator.*"}[5m]))
) > 0.05
and sum(rate(http_server_requests_seconds_count{job="api", uri!~"/actuator.*"}[5m])) > 0
```

`for: 5m`, `severity: critical`.

`api`'s own non-5xx rate (`outcome="SERVER_ERROR"`, Micrometer's own tag),
excluding `/actuator/*` so the scrape/health traffic never counts against
it, has stayed above 5% for a sustained 5 minutes while real request
traffic underneath was non-zero (the `> 0` traffic guard exists so a
single failed manual test request can't swing a near-zero-traffic ratio
to 100% for one scrape). See ADR 0020's SLO table.

## What it means in practice

`api` itself is the one returning 5xx to a real share of its callers —
this is `api`'s own failure, not a downstream dependency's (that's
`ApiVariantsLookupHighErrorRate`'s job to distinguish). Anyone calling
`api`'s HTTP surface (`POST /work-items`, `GET /work-items/{id}`, etc.)
is failing at a rate above the dashboard's own "critical" threshold
(ADR 0017).

## First response

1. Check `api`'s own logs first, not a downstream dependency — this
   rule is scoped to `api`'s own 5xx, so start there:
   ```
   kubectl logs -n api deploy/api --tail=200
   ```
2. Confirm the pod is actually up and not mid-restart/crashlooping:
   ```
   kubectl get pods -n api
   ```
3. **Check for the Postgres Secret drift failure mode seen before**
   (platform#34/#40) — a fresh `api` pod's first connection attempt can
   hit `FATAL: password authentication failed for user "api"` if the
   `postgresql` Secret has silently diverged from the running database's
   actual password (confirmed to happen even with `ignoreDifferences` in
   place, since that only stops *future* drift, not a pre-existing
   mismatch). Compare directly:
   ```
   kubectl exec -n api postgresql-0 -- env | grep PASSWORD
   kubectl get secret postgresql -n api -o jsonpath='{.data.password}' | base64 -d
   ```
   If they don't match, fix live the same way the original incident was
   fixed — `ALTER USER api WITH PASSWORD '<Secret's value>'` against the
   running Postgres — rather than assuming a restart alone will help.
4. Check `api`'s own traces (Tempo) or `/actuator/health` for which
   downstream dependency (Postgres, Redis, Kafka) is actually failing:
   ```
   kubectl exec -n api deploy/api -- curl -s localhost:8080/actuator/health
   ```

## How to confirm resolution

1. Re-query the same expression against the live Prometheus (port-forward
   `svc/prometheus-server -n prometheus`) and confirm the ratio has
   dropped back under 0.05:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=
     sum(rate(http_server_requests_seconds_count{job="api", outcome="SERVER_ERROR", uri!~"/actuator.*"}[5m]))
     /
     sum(rate(http_server_requests_seconds_count{job="api", uri!~"/actuator.*"}[5m]))'
   ```
2. Confirm the alert itself has cleared in Alertmanager (not just the
   underlying metric):
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ApiHighErrorRate
   ```
   (`0` once resolved, or the alert entry's `status.state` reads
   `resolved` rather than `active`.)
3. A real request against `api`'s actual HTTP surface (e.g.
   `GET /work-items/{id}` on a known-good item) returning 2xx directly
   confirms the fix, not just the metric moving.
