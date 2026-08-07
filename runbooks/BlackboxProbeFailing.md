# BlackboxProbeFailing

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
probe_success{job=~"blackbox-.*"} == 0
```

`for: 5m`, `severity: critical`.

One of `blackbox-exporter`'s (backlog #93) real HTTP probes has failed
for 5 minutes straight. `probe_success` is a real 0/1 result from an
actual outbound HTTP request `blackbox-exporter` made through the
cluster's real Ingress/TLS/routing chain — not a self-generated-traffic
proxy, and not the target service's own health endpoint.

Three jobs share this one alert (`blackbox-http-2xx`,
`blackbox-http-2xx-auth`, `blackbox-http-401-expected`) — the failing
one, and the exact URL, are both real labels on the firing series
(`job`, `instance`), not something to guess from the alert name alone.

## What it means in practice

- **`blackbox-http-2xx`** (aggregator/grafana/visualizer/clinvar-viewer):
  a real GET to that exact URL didn't return 2xx.
- **`blackbox-http-2xx-auth`**: the "blackbox" tenant's own real API key
  didn't authenticate successfully against `api`'s real auth chain.
- **`blackbox-http-401-expected`**: an unauthenticated request that
  should have been rejected with 401 got something else — either `api`
  started allowing unauthenticated access (a real, serious regression,
  not a minor one) or the endpoint itself broke in some other way.

This probe crosses more real infrastructure than the target's own
health check does: DNS resolution (via `blackbox-exporter`'s own
`hostAliases`, since these hostnames only resolve on the operator's
laptop otherwise), TLS handshake against the real cluster CA, and
Traefik's own Host-header routing — a failure here can mean any of
those layers, not just the target application itself.

## First response

1. Identify exactly what failed from the alert's own real labels
   (`{{ $labels.job }}`, `{{ $labels.instance }}`), then reproduce
   directly:
   ```
   curl --cacert adamastorx-ca.crt https://<the failing instance URL>
   # add -u blackbox:<key> if the failing job is blackbox-http-2xx-auth
   ```
2. If the direct `curl` also fails the same way, the target service or
   Traefik/cert-manager is the real problem — check the target's own
   pod health first, then Traefik:
   ```
   kubectl get pods -n <target-namespace>
   kubectl get pods -n traefik
   kubectl get certificate -A   # cert-manager renewal failure would show here
   ```
3. If the direct `curl` succeeds but the alert is still firing, the
   problem is specific to `blackbox-exporter` itself — check its own
   pod, and specifically whether its `hostAliases`/CA ConfigMap mount
   are still correct (a Traefik Service ClusterIP change would break
   every probe at once, not just one):
   ```
   kubectl get pods -n blackbox-exporter
   kubectl logs -n blackbox-exporter deploy/blackbox-exporter
   kubectl get svc -n traefik traefik -o jsonpath='{.spec.clusterIP}'
   ```
4. For `blackbox-http-2xx-auth` specifically: if the direct `curl` with
   the key also gets rejected, the "blackbox" tenant's key itself may
   have been rotated or removed from `api-tenant-keys` without updating
   the `blackbox-api-key` Secret this exporter reads — check both
   Secrets agree.

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=probe_success{job=~"blackbox-.*"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c BlackboxProbeFailing
   ```
