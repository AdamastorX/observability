# Chaos / failure-injection scenarios (backlog #23)

Three scenarios (trimmed from the original seven, ADR 0021/S6): Kafka
broker unavailable, PostgreSQL unavailable/PVC full, consumer-group lag.
Each gets a fact-pack doc here — real commands, real timestamps, real
log output, not a narrative reconstructed after the fact.

Executed against the live cluster (`KUBECONFIG=~/.kube/config`), with
explicit confirmation before every fault injection.

**Standing step (backlog #89)**: capture 2-3 images into `docs/assets/`
at incident time and link them from the fact pack. A fact pack is
~90% of a publishable article but stays text-only without this —
producing the article later means re-running the incident just for
the visuals, which a chaos scenario shouldn't need to do twice.

| Scenario | Status | Doc |
|---|---|---|
| 1. Kafka broker unavailable | Done | [`01-kafka-broker-unavailable.md`](01-kafka-broker-unavailable.md) |
| 2. PostgreSQL unavailable (PVC-full found untestable on this cluster) | Done | [`02-postgresql-unavailable.md`](02-postgresql-unavailable.md) |
| 3. Consumer-group lag | Done | [`03-consumer-lag.md`](03-consumer-lag.md) |
