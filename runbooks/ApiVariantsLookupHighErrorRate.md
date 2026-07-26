# ApiVariantsLookupHighErrorRate

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(
  sum(rate(http_server_requests_seconds_count{job="api", outcome="SERVER_ERROR", uri="/variants/lookup"}[5m]))
  /
  sum(rate(http_server_requests_seconds_count{job="api", uri="/variants/lookup"}[5m]))
) > 0.10
and sum(rate(http_server_requests_seconds_count{job="api", uri="/variants/lookup"}[5m])) > 0
```

`for: 5m`, `severity: critical`.

`api`'s `GET /variants/lookup` endpoint specifically has had a non-5xx
rate above 10% for a sustained 5 minutes (higher bar than the 5%
service-wide `ApiHighErrorRate` threshold, since this single endpoint
sees much lower, noisier traffic in this dev cluster). See ADR 0020's
SLO table.

## What it means in practice

This endpoint's whole job is calling out to `clinvar-service`'s
`GET /internal/clinvar/lookup` and fronting the result with `api`'s Redis
cache-aside layer (ADR 0016). This is `api`'s own named external-dependency
failure mode (ADR 0020) — the bug is very likely in `clinvar-service` or
the network path to it, not in `api` itself.

## First response

1. **Check `clinvar-service`'s own health/logs before assuming the bug is
   in `api`** — this is the ADR 0020 rule for this alert specifically:
   ```
   kubectl get pods -n clinvar
   kubectl logs -n clinvar deploy/clinvar-service --tail=200
   ```
2. Confirm `clinvar-service`'s `/internal/clinvar/lookup` actually
   responds, independent of `api`:
   ```
   kubectl exec -n clinvar deploy/clinvar-service -- curl -s -o /dev/null -w '%{http_code}\n' \
     'localhost:8000/internal/clinvar/lookup?rsid=rs80357906'
   ```
3. If `clinvar-service` itself looks healthy, check `clinvar-service`'s
   own dedicated Postgres (`clinvar_release`/`clinvar_variant_index`) —
   same Secret-drift failure mode seen on `api`'s Postgres (platform#34)
   is possible here too, since both are Bitnami-chart-managed:
   ```
   kubectl exec -n clinvar clinvar-postgresql-0 -- env | grep PASSWORD
   kubectl get secret clinvar-postgresql -n clinvar -o jsonpath='{.data.password}' | base64 -d
   ```
4. Only after `clinvar-service` and its dependency are ruled out, check
   `api`'s own side of the call (its Redis cache-aside layer, or the
   HTTP client itself):
   ```
   kubectl logs -n api deploy/api --tail=200 | grep -i "clinvar\|lookup"
   ```

## How to confirm resolution

1. Re-query the same ratio against live Prometheus and confirm it's back
   under 0.10:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=
     sum(rate(http_server_requests_seconds_count{job="api", outcome="SERVER_ERROR", uri="/variants/lookup"}[5m]))
     /
     sum(rate(http_server_requests_seconds_count{job="api", uri="/variants/lookup"}[5m]))'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ApiVariantsLookupHighErrorRate
   ```
3. A real `GET /variants/lookup?rsid=<known-good rsID>` through `api`
   returning the expected ClinVar classification (2xx, real payload, not
   a cached stale error) is the concrete end-to-end confirmation.
