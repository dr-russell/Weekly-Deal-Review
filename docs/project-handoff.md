# Deal Review Control Center Handoff

This repository contains the local Control Center and its supporting extraction,
research, review, verification, publication, and evaluation code.

## Structure

- `control_center/`: FastAPI backend, model orchestration, Google publisher, and tests.
- `control_center/web/`: React interface.
- `control_center/references/`: investment mandate and newsletter-source configuration.
- `docs/`: workflow notes, screenshot, and publication-readiness review.

## Continue Working

Follow the [setup instructions](../control_center/README.md). Configure the
ignored Google local settings and OAuth files, provision the private portfolio
and historical reference assets, and authorize the configured account.
Start the app, choose an IC week, run Prepare Data, then Review & Publish.

Only final Yes rows are published. Every routed Maybe must resolve to Yes or No.
Use one finalized set for the Sheet, Stats, and roundup. Preserve original source
evidence, correction memory, and failure details in the local audit.

Credentials, source bodies, portfolio exports, run artifacts, and SQLite data
are excluded from Git. A fresh clone does not restore them. No public deployment
or credentials-free sample mode is included.
