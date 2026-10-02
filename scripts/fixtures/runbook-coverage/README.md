# Fixture: deliberately-broken runbook coverage

Used by `.github/workflows/ci.yml`'s self-test step to prove
`check-runbook-coverage.sh` actually fails on the two real shapes
backlog #111 found live: an alert with no runbook file at all
(`MissingRunbookEntirely`), and an alert with a real runbook file that
the README's own table never mentions (`HasRunbookNotListedInReadme`).
A third alert (`HasRunbookAndListed`) is included so the fixture isn't
trivially all-broken -- proves the check doesn't just fail on anything.
