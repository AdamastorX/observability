# WatchlistDlqDepthHigh

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
watchlist_delivery_dlq_depth{job="watchlist-service"} > 0
```

`for: 5m`, `severity: warning`.

At least one subscriber notification (ADR 0026's outbox-table-plus-
relay pattern, backlog #53) has been dead-lettered — moved to
`DEAD_LETTERED` after `app.delivery.max-attempts` failed real delivery
attempts to that subscriber's own `ntfy` target — and stayed there for
5 minutes. `watchlist_delivery_dlq_depth` is a real gauge, not a
counter: it reflects the current dead-lettered count, so it can also
go back down (a human clearing a stale subscription, or a re-triggered
delivery) without a restart.

## What it means in practice

**This is per-subscriber, by design — it does not mean the whole
pipeline is broken.** ADR 0026's own dead-lettering design exists
specifically so one permanently-broken subscriber's target (a deleted
`ntfy` topic, a typo'd subscription) never blocks any other
subscriber's fan-out — every delivery is its own row in the
`deliveries` table, processed independently by `NotificationRelay`.
A firing alert means at least one real subscriber is not receiving
notifications they should be — a real, user-facing gap, but a bounded
one, not a systemic outage.

## First response

1. Find which subscription(s) are actually dead-lettered — the alert's
   own gauge doesn't say which one:
   ```
   kubectl exec -n watchlist watchlist-postgresql-0 -- \
     psql -U watchlist -d watchlist -c \
     "SELECT id, subscription_id, release_id, variant_key, attempts, updated_at FROM deliveries WHERE status = 'DEAD_LETTERED' ORDER BY updated_at DESC;"
   ```
2. Check `watchlist-service`'s own logs around the last delivery
   attempt for that row — the real reason ntfy rejected/failed the
   call (a 4xx from a deleted/renamed topic, a network timeout, a
   real ntfy outage) is what determines the right fix:
   ```
   kubectl logs -n watchlist deploy/watchlist-service --tail=200
   ```
3. If the target `ntfy` topic itself is the problem (deleted, renamed,
   never subscribed-to by the intended recipient), this is a real
   subscriber-side data issue, not a platform bug — the subscription's
   own target needs correcting via the real subscription API, not a
   retry.
4. If many unrelated subscriptions are dead-lettering at once, suspect
   a real, broader `ntfy.sh` outage or a `watchlist-service`-side bug
   in the relay itself rather than N independent subscriber-side
   problems — check `NotificationRelay`'s own recent logs for a
   pattern across rows, not just the one row investigated above.

## How to confirm resolution

1. Re-query the gauge directly:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=watchlist_delivery_dlq_depth{job="watchlist-service"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c WatchlistDlqDepthHigh
   ```
3. If a dead-lettered row was manually corrected and should be
   retried, confirm it actually left `DEAD_LETTERED` in Postgres (this
   service has no automatic dead-letter-to-pending requeue path today —
   a manual `UPDATE` back to `PENDING`, or a fresh event, is what
   actually clears a specific row):
   ```
   kubectl exec -n watchlist watchlist-postgresql-0 -- \
     psql -U watchlist -d watchlist -c \
     "SELECT status FROM deliveries WHERE id = '<the row's real id>';"
   ```
