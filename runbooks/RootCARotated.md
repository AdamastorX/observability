# RootCARotated

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`,
platform#181).

## What fired

```
changes(certmanager_certificate_expiration_timestamp_seconds{name="adamastorx-root-ca", namespace="cert-manager"}[1h]) > 0
```

`for: 0m`, `severity: critical` — fires immediately, no debounce. A root
CA is expected to be static for its entire 10y `Duration`
(`platform/kubernetes/cert-manager-issuers/`'s `adamastorx-root-ca`
Certificate; real `Renewal Time` in `kubectl describe certificate`
reads a decade out). Any change to this gauge's value means
cert-manager actually reissued the root — not a routine event, an
"every client's local TLS trust just went stale" event.

## What it means in practice

Found live, backlog #139: on 2026-08-10 backlog #49's cluster rebuild
re-bootstrapped the `cert-manager` namespace from scratch and minted a
**brand-new** self-signed root — not cert-manager's own renewal logic
firing, a fresh CA with a different keypair and a different
fingerprint. Nothing alerted. Six days later a human hit a broken
`visualizer` UI ("could not reach aggregator.local.adamastorx.test")
that turned out to be nothing to do with the cluster — `aggregator`'s
pod and Ingress were both healthy, the TLS handshake reached the
server fine, but every local client (system `curl`, Chrome's own NSS
store, Firefox's own store) still trusted the *old* root and rejected
the new one with "unknown CA."

This alert exists so the next rotation — planned or not — produces a
page instead of a multi-day silent break. It fires on the fact of a
change, not on whether the change was intentional; a deliberate,
announced CA rotation still fires this and still needs every client
re-trusted, which is the correct behavior, not a false positive.

## First response

1. Confirm it's real, not a stale/duplicate scrape:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=certmanager_certificate_expiration_timestamp_seconds{name="adamastorx-root-ca"}'
   ```
   Compare the returned Unix timestamp against what's currently trusted
   locally:
   ```
   openssl x509 -in /home/lmpeixoto/repos/AdamastorX/platform/adamastorx-ca.crt -noout -enddate
   ```
   If they don't match (or the local file doesn't exist/is stale), the
   rotation is real and every local client's trust is now broken.
2. Confirm cert-manager's own view agrees, and check *when* it happened
   (helps correlate with a real cause — a deliberate rotation, a
   `kubectl delete secret`, or a namespace-recreating rebuild like #49):
   ```
   kubectl describe certificate -n cert-manager adamastorx-root-ca
   ```
   `Status.Not Before` / the Secret's own `creationTimestamp`
   (`kubectl get secret -n cert-manager adamastorx-root-ca -o jsonpath='{.metadata.creationTimestamp}'`)
   pin the real moment. If it lines up with unrelated infra work
   (a rebuild, a `cert-manager` namespace change), that's very likely
   the cause, not a security incident on its own — but don't assume
   without checking; a root Secret deleted outside any known change is
   worth treating as a real question, not routine.

## Remediation — re-trust the new CA everywhere

Three separate trust stores need the new CA, not one
(`platform/kubernetes/cert-manager-issuers/README.md` has the original
first-time-setup version of this; this is the same steps run as an
incident response, not a fresh setup):

```sh
# 1. Regenerate the local convenience copy from the live Secret
kubectl get secret -n cert-manager adamastorx-root-ca \
  -o jsonpath='{.data.ca\.crt}' | base64 -d \
  > /home/lmpeixoto/repos/AdamastorX/platform/adamastorx-ca.crt

# 2. System store (curl and most Linux tools)
sudo cp /home/lmpeixoto/repos/AdamastorX/platform/adamastorx-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# 3. Chrome/Chromium -- own NSS store, not the system one above
certutil -d sql:$HOME/.pki/nssdb -D -n "adamastorx-ca"   # drop the stale entry if present
certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "adamastorx-ca" \
  -i /home/lmpeixoto/repos/AdamastorX/platform/adamastorx-ca.crt
# fully quit and reopen Chrome, not just the tab

# 4. Firefox -- own store too, no CLI: Settings -> Privacy & Security ->
# Certificates -> View Certificates -> Authorities -> remove the old
# adamastorx-ca entry if listed, then Import the new file.
```

Blackbox-exporter's own synthetic probes (`BlackboxProbeFailing`) use
the same cluster CA to verify the services they probe — if this alert
and `BlackboxProbeFailing` fire together, that's the same root cause,
not two incidents; fixing the CA trust above resolves both.

## How to confirm resolution

1. ```
   curl --cacert /home/lmpeixoto/repos/AdamastorX/platform/adamastorx-ca.crt \
     https://aggregator.local.adamastorx.test/aggregates -o /dev/null -w '%{http_code}\n'
   ```
   should read `200`.
2. Same call **without** `--cacert` (relying on the now-updated system
   store) should also succeed with no TLS error.
3. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c RootCARotated
   ```
4. The alert's own `for: 0m` means it clears as soon as the underlying
   `changes(...[1h])` window rolls past the rotation event (up to 1h
   after the fact) — it does not need to be manually silenced, but
   don't close the incident on the alert clearing alone if step 1/2
   above haven't actually been re-verified.
