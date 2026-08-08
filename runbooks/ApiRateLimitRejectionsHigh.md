# ApiRateLimitRejectionsHigh

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(sum(rate(traefik_entrypoint_requests_total{code="429",entrypoint="websecure"}[5m])) or vector(0)) > 0.2
```

`for: 5m`, `severity: warning`.

Traefik's own `websecure` entrypoint (the public HTTPS ingress, ADR
0021) has averaged over 0.2 requests/second returning `429` (rate
limited) for a sustained 5 minutes. `api-key-ratelimit`
(`kubernetes/api/middlewares.yaml`, backlog #56) is the real,
currently-only source of `429`s at this entrypoint: 5 req/s average,
burst 10, keyed per-tenant on the `Authorization` header.

**Entrypoint-wide, not per-tenant, by design** (ADR 0027, adamastorx
repo): Traefik's own rate-limit metric carries no per-tenant/API-key
label (a deliberate cardinality-explosion guard on Traefik's own
part), so this alert cannot distinguish "one misbehaving tenant" from
"many tenants each individually within their own budget, summing past
this entrypoint-wide threshold" from the metric alone.

## What it means in practice

Real client traffic is hitting the per-key rate limit hard enough,
sustained, to be worth a human look — either a real tenant is sending
more traffic than their budget allows (a misbehaving client, a bug in
a consumer's own retry logic, or a real legitimate traffic spike that
now needs a budget conversation), or `workload-generator`'s own
continuous synthetic traffic (backlog #45) is itself tripping its own
key's limit, which is a real, expected, low-stakes case worth ruling
out first since it runs continuously by design.

## First response

1. Identify which tenant(s) are actually being rejected — the alert's
   own entrypoint-wide metric doesn't say which key, but Traefik's
   real JSON access logs do (shipped to Loki via Alloy, backlog #56):
   ```
   curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
     --data-urlencode 'query={namespace="traefik"} | json | DownstreamStatus="429"' \
     --data-urlencode 'limit=50'
   ```
   The `ClientUsername` field on each matching log line is the real
   tenant name (HTTP Basic auth username, `api-key-auth`).
2. If it's `workload-generator`'s own key, this is expected, low-stakes
   background noise from its own continuous traffic — confirm its
   configured rate (`workload-generator-config` ConfigMap,
   `target_rps`) hasn't been bumped inadvertently, rather than treating
   it as a real incident:
   ```
   kubectl get configmap workload-generator-config -n workload-generator -o yaml
   ```
3. If it's a real external tenant, confirm whether this is a genuine
   traffic spike (check `traefik_entrypoint_requests_total` broken
   down by `code` over the same window for that tenant's real request
   volume) or a stuck client retrying aggressively into its own rate
   limit rather than backing off:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sum(rate(traefik_entrypoint_requests_total{entrypoint="websecure"}[5m])) by (code)'
   ```
4. If the real, sustained traffic is legitimate and simply needs more
   budget, `api-key-ratelimit`'s `average`/`burst` values
   (`kubernetes/api/middlewares.yaml`) are the real, deliberate,
   currently-shared-across-all-tenants budget (ADR 0027) — raising it
   is a real, considered platform change, not a quick live patch to
   make during an active alert.

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=sum(rate(traefik_entrypoint_requests_total{code="429",entrypoint="websecure"}[5m]))'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ApiRateLimitRejectionsHigh
   ```
3. Confirm the specific tenant identified in step 1 above is now
   getting real `200`/`202` responses, not just that the aggregate
   rate dropped:
   ```
   curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
     --data-urlencode 'query={namespace="traefik"} | json | ClientUsername="<tenant>"' \
     --data-urlencode 'limit=20'
   ```
