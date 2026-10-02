# NodeDiskSpaceCritical

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) < 0.10
```

`for: 15m`, `severity: critical`.

The same real root-filesystem signal `NodeDiskSpaceLow` watches, a
second, faster-paging tier — free space has stayed below 10%, not just
20%.

## What it means in practice

This is not just "a PVC owner should clean up soon." A full disk on
this single-node cluster can break k3s/etcd itself — the control plane
that everything else, including Prometheus reading this very alert,
depends on. Treat this as urgent: identifying and shrinking/evicting
the largest real consumer takes priority over root-causing exactly why
it grew.

## First response

Same identification steps as `NodeDiskSpaceLow.md` (`kubectl get pvc -A
--sort-by=...`, `du -sh` inside the largest consumer), but act on the
result immediately rather than just recording it — if the responsible
process is still writing, stop it first, then reclaim space:

```
kubectl get pvc -A --sort-by=.spec.resources.requests.storage
df -h /
kubectl exec -n <namespace> <pod> -- du -sh /path/to/volume/mount/* | sort -rh | head
```

If no single owner is obviously responsible and the node is close to
actually full, the fastest safe reclaim on this cluster is usually
`local-path`'s own provisioner directory (orphaned volumes from deleted
PVCs it may not have cleaned up) — check before assuming a live
service is the sole cause.

## How to confirm resolution

1. Re-query the same expression against live Prometheus:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}'
   ```
2. Confirm both this alert and `NodeDiskSpaceLow` have cleared in
   Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c NodeDiskSpace
   ```
3. Once clear, do the root-cause step `NodeDiskSpaceLow.md` describes
   (grow the PVC, shorten retention, fix a leak) — this runbook only
   covers the urgent reclaim, not the durable fix.
