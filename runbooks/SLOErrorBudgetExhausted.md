# SLOErrorBudgetExhausted

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).
Backlog #181; the SLOs and their objectives are declared in ADR 0020's
2026-10-10 addendum.

## What fired

```
slo:error_budget_remaining:ratio7d < 0
```

`for: 1h`, `severity: warning`. One alert per SLO; the `slo` and `service`
labels say which. The series comes from the recording rules in
`serverFiles["recording_rules.yml"]` (backlog #177): over the last 7 days
the SLO's good-event ratio is below its objective, so its error budget is
spent. This is **not** a fast-detection alert. The threshold alerts
(`ApiHighErrorRate` and the others) catch an incident as it happens; this one
says the week as a whole has not met the target.

## What it means in practice

The service has been worse than its objective for long enough that the 7-day
average is below target. It may be a past incident still inside the window
(the budget recovers as bad days leave the window), or an ongoing low-grade
problem the threshold alerts are too coarse to catch.

## First response

1. Open the **SLOs** dashboard in Grafana and find the row: compliance,
   objective, events in the window.
2. Find out whether it is still happening or is old news:
   ```
   curl -s -G http://localhost:9090/api/v1/query --data-urlencode \
     'query=slo:compliance:ratio7d{slo="<slo>"}'
   ```
   then look at the same SLI over the last hour on the service's own
   Golden Signals dashboard (linked from **Service health**).
3. If it is ongoing, treat it as an incident for that service: use that
   service's own alert runbook (for example `ApiHighErrorRate`).
4. If it is a past incident still in the window, nothing needs fixing now.
   Note it in the next operations review and let the window roll.
5. If the objective itself looks wrong (it cannot be met by a healthy
   system), that is a calibration question for backlog #137, not a reason to
   change the rule silently.

## How to confirm resolution

```
curl -s -G http://localhost:9090/api/v1/query --data-urlencode \
  'query=slo:error_budget_remaining:ratio7d{slo="<slo>"}'
```

The value is back above 0, and the alert has cleared in Alertmanager:

```
curl -s http://localhost:9093/api/v2/alerts | grep -c SLOErrorBudgetExhausted
```
