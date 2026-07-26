# ClinVarIngestionFreshnessBreach

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
increase(clinvar_ingestion_duration_seconds_count{job="clinvar-service"}[8d]) < 1
```

`for: 0m`, `severity: critical`.

No completed ClinVar ingestion run (success or failure) has been
recorded by `clinvar-service` in over 8 days (7-day weekly cadence plus
a one-day grace period before treating a late run as a real breach
rather than ordinary scheduling jitter). See ADR 0020's SLO table.

**Known, stated limitation** (not fixed by this runbook): because
`clinvar_ingestion_duration_seconds_count`'s `.time()` wrapper
(`app/ingestion.py`) increments on both the success *and* failure path,
this rule can only detect "no ingestion attempt at all in 8 days" — it
cannot detect "attempts happened but every one failed." A
success-only signal to close that gap is tracked separately as backlog
#21e, not built here.

## What it means in practice

`clinvar-service`'s weekly scheduled ingestion trigger may be silently
dead — the source data (ClinVar's VCF release) is going stale with
nothing surfacing it except this alert. This is also the exact metric
surface built in direct response to the double-ingestion incident (two
overlapping manual triggers ran two concurrent VCF scans, invisible for
the ~90s the slowest step normally takes, ending in a SIGKILL) — so
this alert firing is a real, previously-blind spot now being watched.

## First response

1. Check `clinvar-service`'s own logs and APScheduler state directly —
   this is the specific guidance already written into the rule's own
   comment in `prometheus.yaml`:
   ```
   kubectl logs -n clinvar deploy/clinvar-service --tail=200
   ```
2. **Check for the double-ingestion / concurrent-run lock first** —
   services#36 added a lock so `ingest()` now rejects a second concurrent
   call with `409` rather than silently overlapping. If the scheduler's
   automatic trigger is being rejected by that same lock because a stuck
   or still-running previous ingestion never released it, that's the
   most likely reason a new run never records a completion:
   ```
   kubectl exec -n clinvar deploy/clinvar-service -- curl -s localhost:8000/metrics | grep clinvar_ingestion_in_progress
   ```
   A gauge stuck at `1` well past the ~90s real-data baseline duration
   means a prior run is stuck holding the lock, not that the scheduler
   never fired.
3. Check whether the pod itself restarted (a SIGKILL would show as a
   restart with **no OOM evidence** in the usual places — this project's
   own double-ingestion incident confirmed `dmesg`/`journalctl -k`/
   `journalctl -u k3s.service` were all clean around the exact event, so
   don't rely on the usual "check dmesg for OOM" playbook alone):
   ```
   kubectl get pods -n clinvar
   kubectl logs -n clinvar deploy/clinvar-service --previous --tail=200
   ```
4. If the pod and lock both look clean, check whether the scheduler
   itself is even configured/running (APScheduler job list, if exposed)
   rather than assuming the weekly trigger fired and failed silently.

## How to confirm resolution

1. Trigger (or wait for) a real ingestion run to complete, then re-query
   live Prometheus directly for a fresh count:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=increase(clinvar_ingestion_duration_seconds_count{job="clinvar-service"}[8d])'
   ```
   Should read `>= 1` once a real completion (success or failure) has
   been recorded.
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ClinVarIngestionFreshnessBreach
   ```
3. Since this rule can't distinguish success from failure (the stated
   limitation above), also confirm the completed run actually
   *succeeded* — check `clinvar-service`'s logs for the run's outcome, or
   confirm a real `GET /variants/lookup` against a recently-changed
   record reflects the new release, not stale data from before the gap.
