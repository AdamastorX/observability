# NodeDiskSpaceLow

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) < 0.20
```

`for: 15m`, `severity: warning`.

The node's real root filesystem (`/`, backed by `/dev/sda2`, 250GB —
confirmed live, not `/boot/efi` or a tmpfs pseudo-filesystem
node-exporter also reports) has stayed below 20% free for 15 minutes.

This metric did not exist as a scraped series at all until backlog
#92 enabled `kube-state-metrics`/`node-exporter` (2026-08-07) — before
that, backlog #21d's own AC ("whichever metric is already scraped")
had nothing real to alert against.

## What it means in practice

Every PVC on this cluster (both Postgres instances, Loki, Tempo,
`clinvar-service`'s refdata, Prometheus's own, Kafka's new one per ADR
0032) uses the `local-path` StorageClass, confirmed to enforce no
storage quota — a PVC's own `requests.storage` is advisory only.
Unbounded growth on any single one of them silently eats this shared,
single-node disk with no per-service warning of its own; this alert is
the only node-level backstop. Real headroom today is ~70% free, so
crossing 20% means real, sustained, abnormal growth somewhere — not
routine log rotation or a one-off write burst (the 15m window already
filters those out).

## First response

1. Find the largest real consumer, not by guessing:
   ```
   kubectl get pvc -A --sort-by=.spec.resources.requests.storage
   df -h /   # from a node shell, or via a debug pod
   ```
2. Once the likely PVC/pod is identified, check inside it for what's
   actually growing (a runaway log file, an unbounded cache, an
   ingestion job that isn't rotating old data):
   ```
   kubectl exec -n <namespace> <pod> -- du -sh /path/to/volume/mount/* | sort -rh | head
   ```
3. If it's a real, expected growth (e.g. Prometheus's own retention,
   backlog #94) rather than a bug, this is a real capacity-planning
   signal, not a false alarm — decide whether to grow the PVC or
   shorten retention, and record the decision.
4. If it's unexpected growth, stop the responsible process/CronJob
   first, then clean up, rather than deleting data blind while it's
   still being written.

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c NodeDiskSpaceLow
   ```
3. See `NodeDiskSpaceCritical.md` if free space keeps dropping past
   10% — the same signal, a faster-paging tier, not a separate
   incident.
