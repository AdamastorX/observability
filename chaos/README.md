# Chaos / failure-injection scenarios (backlog #23)

Three scenarios (trimmed from the original seven, ADR 0021/S6): Kafka
broker unavailable, PostgreSQL unavailable/PVC full, consumer-group lag.
Each gets a fact-pack doc here — real commands, real timestamps, real
log output, not a narrative reconstructed after the fact.

Executed against the live cluster (`KUBECONFIG=~/.kube/config`), with
explicit confirmation before every fault injection.

| Scenario | Status | Doc |
|---|---|---|
| 1. Kafka broker unavailable | Done | [`01-kafka-broker-unavailable.md`](01-kafka-broker-unavailable.md) |
| 2. PostgreSQL unavailable (PVC-full found untestable on this cluster) | Done | [`02-postgresql-unavailable.md`](02-postgresql-unavailable.md) |
| 3. Consumer-group lag | Done | [`03-consumer-lag.md`](03-consumer-lag.md) |
