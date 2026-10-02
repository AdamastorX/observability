# KafkaBrokerUnavailable

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
probe_success{job="blackbox-kafka-tcp"} == 0
```

`for: 2m`, `severity: critical`.

A real TCP connect from `blackbox-exporter` to
`kafka.kafka.svc.cluster.local:9092` has failed for 2 minutes straight
(backlog #42). Kafka has no Prometheus metrics of its own in this
cluster (no JMX exporter/sidecar deployed — standing one up is real,
separate infra scope this alert deliberately didn't need to answer "is
the broker reachable"). This reuses the already-deployed, already-proven
`blackbox-exporter` with a plain `tcp_connect` module instead.

`for: 2m` here, not `BlackboxProbeFailing`'s 5m: every real
producer/consumer in this cluster (`api`, `workers`, `clinvar-service`,
`watchlist-service`, `market-data-ingestor`, `news-ingestor`,
`sentiment-analyzer`, `aggregator`) depends on this one broker, with no
fallback — a real outage here is a whole-cluster event, not a single
endpoint, and deserves a faster page.

## Why this alert exists, and what it's not

Chaos scenario 1 (`observability/chaos/01-kafka-broker-unavailable.md`,
backlog #23) found that `ApiHighErrorRate` alone needs a sustained 5m
window of real, non-zero traffic at >5% error rate to trip — under this
project's earlier low/manual traffic pattern, a brief Kafka outage never
accumulated that. Backlog #45's permanent workload generator later
closed that specific gap for this cluster's actual real failure mode
(a broker restart losing ephemeral-storage topics produces sustained
downstream errors that do trip `ApiHighErrorRate` on their own,
confirmed live in ~8m44s). This alert is kept anyway, deliberately, as
real defense-in-depth: it generalizes to a brief-outage shape this
cluster doesn't currently produce but could (a fast broker restart that
resolves before 5m of elevated error rate accumulate), and it fires
independent of whether any real traffic happens to be flowing at the
moment — `ApiHighErrorRate` still needs real requests to fail against.

## First response

1. Confirm the pod itself, first — the most common real cause in this
   project's own history:
   ```
   kubectl get pods -n kafka
   ```
2. If the pod looks `Running`/`Ready` but this alert is still firing,
   check the broker's own logs for the real error:
   ```
   kubectl logs -n kafka kafka-controller-0
   ```
3. If the broker answers a plain TCP connect (this alert clears) but a
   *specific* real flow is still broken, the problem is more likely a
   missing/misconfigured topic than broker-wide unavailability — confirm
   the real topic list directly:
   ```
   kubectl exec -n kafka kafka-controller-0 -- kafka-topics.sh \
     --bootstrap-server localhost:9092 --list
   ```
   (`docs/SESSION_STATE.md` in the `adamastorx` repo has the real,
   sanctioned fallback for provisioning a missing topic manually if
   ArgoCD's own PostSync hook doesn't re-fire against a no-diff sync.)
4. Check whether this is a real, single-node capacity story rather than
   a Kafka-specific failure — `kubectl describe node` /
   `kubectl top pod -n kafka` (ADR 0040/0041's own documented hardware
   ceiling; a real, live-confirmed contributor to more than one incident
   already in this project's history).

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=probe_success{job="blackbox-kafka-tcp"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c KafkaBrokerUnavailable
   ```
3. Spot-check one real downstream consumer actually resumed (not just
   that the TCP port answers) — e.g. `kubectl logs -n workers
   deploy/workers --tail=20` for a real, recent consumed record, or
   trigger a real `POST /work-items` and confirm `workers` picks it up.
