# ClinVarIngestionDurationAnomaly

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(
  rate(clinvar_ingestion_duration_seconds_sum{job="clinvar-service"}[1h])
  /
  rate(clinvar_ingestion_duration_seconds_count{job="clinvar-service"}[1h])
) > 360
and rate(clinvar_ingestion_duration_seconds_count{job="clinvar-service"}[1h]) > 0
```

`for: 2m`, `severity: warning`.

The most recently observed ClinVar ingestion run took over 360 seconds
— 4x the ~90s real-data baseline ("several multiples," ADR 0020's own
wording, not a tuned number against a real historical distribution
this project doesn't have yet). See ADR 0020's SLO table.

## What it means in practice

This is precisely the signal that would have made the double-ingestion
incident visible as a metric instead of something only discoverable by
reading `kubectl logs --previous` after the fact: two overlapping
manual ingestion triggers ran two full VCF scans concurrently, invisible
in logs for the ~90s the slowest step (`_build_variant_index_rows`)
normally takes, ending in a SIGKILL with no OOM evidence anywhere. This
alert firing means an ingestion run is taking multiples longer than
normal right now — either a repeat of that same failure mode, or a
genuinely larger-than-usual source VCF.

## First response

1. **Check for a concurrent/overlapping run first** — the exact shape
   the double-ingestion incident had. services#36's lock now rejects a
   second concurrent `ingest()` call with `409`, so an anomalously long
   run today is less likely to be two overlapping scans, but confirm
   directly rather than assuming the lock fully prevents every slow-run
   scenario:
   ```
   kubectl exec -n clinvar deploy/clinvar-service -- curl -s localhost:8000/metrics | grep -E 'clinvar_ingestion_in_progress|clinvar_ingestion_rejected_total'
   ```
   A non-zero `clinvar_ingestion_rejected_total` around the same time
   window means a second trigger *was* attempted and correctly rejected
   — the long-running one is a single run, not an overlap.
2. Check `clinvar-service`'s own logs for the progress logging
   services#36 added (every 250k records) — this is what makes a stall
   visible now instead of a silent multi-minute gap; a run that's
   genuinely still progressing (not stuck) will show incrementing
   progress lines:
   ```
   kubectl logs -n clinvar deploy/clinvar-service --tail=200 -f
   ```
3. If progress has genuinely stalled (no new log lines for several
   minutes), check for the same "no OOM evidence anywhere" pattern the
   original incident had before assuming a straightforward OOM kill —
   `dmesg -T`, `journalctl -k`, and `journalctl -u k3s.service` around
   the current time, plus node memory headroom, since the original root
   cause was circumstantial (contending large in-memory Python object
   builds against the 768Mi limit), not a single smoking-gun log line:
   ```
   kubectl get pods -n clinvar
   dmesg -T | tail -50
   ```
4. If the run is genuinely just a larger source VCF (not stuck, not
   overlapping), this may not need intervention — let it complete and
   confirm via the resolution steps below.

## How to confirm resolution

1. Re-query the same expression against live Prometheus once the run
   completes (or once a full hour has rolled past the anomalous run)
   and confirm it's back under 360:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=
     rate(clinvar_ingestion_duration_seconds_sum{job="clinvar-service"}[1h])
     /
     rate(clinvar_ingestion_duration_seconds_count{job="clinvar-service"}[1h])'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ClinVarIngestionDurationAnomaly
   ```
3. Confirm `clinvar_ingestion_in_progress` reads `0` (no run still
   stuck) and the completed run's logged outcome was a real success, not
   a slow failure.
