# observability

Grafana dashboards/alerts, OpenTelemetry config, and the runbooks that
respond to those alerts — kept next to each other on purpose so alert and
runbook never drift apart. Separate from
[platform](https://github.com/AdamastorX/platform) deliberately: different
change cadence, different blast radius than cluster infra.

Empty scaffold as of M0. First real content lands with milestone **M3
Observability** — see backlog issues #16–#19 in the `adamastorx` repo's
`docs/roadmap/backlog.md`; runbooks and SLOs follow in **M4** (issues #20–#22).

## Layout

| Dir | Contents |
|---|---|
| `otel/` | OpenTelemetry Collector config |
| `grafana/dashboards/` | Dashboards as code |
| `grafana/alerts/` | Alert rules as code |
| `runbooks/` | One runbook per alert — no alert ships without one (M4) |

## Engineering context

Full project context, workflow, and agent roles: see the `adamastorx` repo's
`.claude/PROJECT.md` and `.claude/WORKFLOW.md`.
