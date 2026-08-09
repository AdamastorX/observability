# ArgoCDAppOutOfSync

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
argocd_app_info{sync_status!="Synced", name!~"clinvar-postgresql|grafana|kafka|postgresql|redis"}
```

`for: 2h`, `severity: warning`.

An ArgoCD Application has reported `sync_status != "Synced"` for over
two hours. ADR 0003 makes ArgoCD the sole GitOps entrypoint for this
cluster — an Application silently stuck out of sync indefinitely means
what's actually running has drifted from what git says should be
running, for longer than any ordinary in-progress sync would ever
take.

**The name exclusion is deliberate and load-bearing, not an
afterthought.** Five real Applications
(`clinvar-postgresql`/`grafana`/`kafka`/`postgresql`/`redis`) are
essentially always `OutOfSync` on this cluster, for a known, accepted
reason: their Bitnami-chart-managed Secret gets a fresh checksum
annotation on every render, which ArgoCD's diff engine treats as real
drift even though the actual credential value never changes. A general
alert on this metric with no exclusion list would fire on those five
permanently, from the moment it shipped — exactly the kind of
always-firing alert that trains a human to ignore the channel. If a
sixth Application starts showing this same benign pattern, add its
name to the exclusion regex here **and** in
`platform/argocd/apps/prometheus.yaml`'s own rule — don't just
silence the page without updating both.

## What it means in practice

Something outside the five known-benign names has drifted from git and
stayed that way for 2+ hours. This alert was built specifically
because of a real, live incident: backlog #94 raised Prometheus's own
retention config (`persistentVolume.size: 2Gi -> 16Gi`), but
`local-path` doesn't support in-place PVC expansion — the live PVC
stays `2Gi` until someone deletes and recreates it, which was declined
for the data loss it costs. That leaves `prometheus`'s own Application
stuck `OutOfSync` indefinitely, with no expiry, until a human acts.

## First response

1. Identify exactly which Application and what's actually different:
   ```
   kubectl get application <name> -n argocd -o json | jq '.status.resources[] | select(.status=="OutOfSync")'
   ```
2. If it's a PVC size mismatch (the known #94 shape): confirm live vs.
   desired size —
   ```
   kubectl get pvc -n <namespace> <pvc-name>
   ```
   against the real `persistentVolume.size`/`storage` value in the
   owning `argocd/apps/*.yaml`. If they differ and the StorageClass is
   `local-path` (no in-place expansion), the fix is a real
   delete+recreate of the PVC, which loses whatever data is on it —
   this is a real, human go/no-go decision, not something to script
   around silently.
3. If it's something else — a Secret/ConfigMap diff not matching the
   five known Bitnami-checksum names, a manifest that failed to apply,
   a chart values change that didn't render — read the real diff from
   step 1 first; don't assume it's the same class of issue as the
   known #94 case just because this alert exists because of that one.
4. A hard refresh sometimes surfaces stale status faster than the next
   poll interval:
   ```
   kubectl annotate application <name> -n argocd argocd.argoproj.io/refresh=hard --overwrite
   ```

## How to confirm resolution

1. ```
   kubectl get application <name> -n argocd -o jsonpath='{.status.sync.status}'
   ```
   should read `Synced`.
2. Re-query the real metric directly:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=argocd_app_info{name="<name>"}'
   ```
   the `sync_status` label on the returned series should read
   `"Synced"`.
3. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c ArgoCDAppOutOfSync
   ```
