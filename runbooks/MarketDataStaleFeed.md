# MarketDataStaleFeed

Source rule: `platform/argocd/apps/prometheus.yaml`
(`server.serverFiles.alerting_rules.yml`, group `adamastorx-slos`).

## What fired

```
market_data_stale_feed{job="market-data-ingestor"} > 0
```

`for: 5m`, `severity: warning`.

`market_data_stale_feed` is a real per-ticker gauge (1/0), fed by
`market-data-ingestor`'s own `MarketHoursService`: it goes to `1` for
a watchlisted ticker only when no real trade tick has been received
within the configured staleness threshold **while US markets are
open** (a simple America/New_York business-hours check, no holiday
calendar). Outside real market hours the gauge stays `0` by design —
after-hours/weekend silence is expected and never reported stale, so
a firing alert genuinely means "the feed should be live right now, and
it isn't."

## What it means in practice

The real-time Finnhub websocket path has gone quiet for at least one
watchlisted ticker during real trading hours — either the websocket
connection itself is down/reconnecting in a loop, or it's connected
but Finnhub itself has stopped sending trades for that specific
symbol (rare, but a real Finnhub-side condition, not necessarily this
project's own bug).

**Do not confuse this with a normal, working REST-poll fallback.**
`FinnhubQuotePoller`'s 30-minute fallback (backlog #87) exists
specifically to cover *off-hours* gaps and does not run during real
market hours in a way that would suppress this alert — if this fires
during real trading hours, the fallback is not expected to be quietly
covering for it.

## First response

1. Check the websocket's own real connection state directly — this
   is the fastest way to tell "never connected" from "connected but
   silent":
   ```
   kubectl logs -n market-data-ingestor deploy/market-data-ingestor --tail=100
   ```
2. Check the real reconnect/failure counters — a climbing
   `websocket_connect_failures_total` with a flat `websocket_reconnects_total`
   means it's stuck failing to (re)connect, not just quiet:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=market_data_websocket_reconnects_total'
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=market_data_websocket_connect_failures_total'
   ```
3. If connect failures are climbing with an `HTTP 429` in the logs,
   this is a real Finnhub-side rate-limit condition, not a bug in this
   project's own code — see backlog #115 for the known, currently
   undersized reconnect-backoff gap this can trip into a
   self-perpetuating lockout. Stopping any further manual pod restarts
   is the fastest real mitigation while the rate-limit window lapses.
4. If the websocket is genuinely connected and healthy but one
   specific ticker is still stale, this may be a real Finnhub-side gap
   for that symbol specifically (illiquid moment, a real feed issue on
   their end) rather than anything wrong on this project's side —
   check whether *all* watchlisted tickers are stale or just one:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=market_data_stale_feed{job="market-data-ingestor"}'
   ```
5. Check the Kafka producer side is not itself the real blocker — a
   permanently poisoned producer (backlog #86, a real, previously-hit
   `LinkageError` from a JVM class-loading failure) would silently
   swallow every real websocket tick it does receive, which can look
   identical to a dead websocket from this gauge's perspective alone:
   ```
   kubectl exec -n market-data-ingestor deploy/market-data-ingestor -- \
     wget -qO- http://localhost:8080/actuator/health/liveness
   ```

## How to confirm resolution

1. Re-query the gauge for every watchlisted ticker:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=market_data_stale_feed{job="market-data-ingestor"}'
   ```
2. Confirm the alert has cleared in Alertmanager:
   ```
   curl -s http://localhost:9093/api/v2/alerts | grep -c MarketDataStaleFeed
   ```
3. Confirm real ticks are actually flowing again, not just the gauge
   resetting on its own:
   ```
   curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
     'query=market_data_ticks_published_total'
   ```
