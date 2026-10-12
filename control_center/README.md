# MetaProp Deal Control Center

A local, evidence-backed workflow for converting newsletter financings into a
consistent deal Sheet, Stats tab, and weekly roundup. Production outputs are
disabled by default. This is not a CRM integration: entity resolution uses the
provided MetaProp portfolio snapshot.

## Run

From the repository root, after completing the prerequisites below:

```sh
control_center/.venv/bin/python -m control_center.launch
```

The launcher prints the localhost URL and reuses an existing control-center
server. It starts a detached server with logs and a PID manifest in `data/`.
The interface is bound to localhost, not publicly exposed.

Dependencies and frontend rebuild:

Google deployment settings are local, not committed. Copy
`control_center/google.local.example.json` to `control_center/google.local.json`
and set the permitted account plus test/production Sheet and Doc IDs. Your
existing local settings are preserved during this migration. Missing settings
stop Google authorization and publication; there is no default personal account
or output target. Local settings are not OAuth credentials; keep credentials in
`control_center/data/secrets/`. The OAuth client file is
`gmail_credentials.json`; Gmail and Google write tokens are stored separately.
The app's mandate and newsletter-source configuration live in
`control_center/references/`.

```sh
python3 -m venv control_center/.venv
control_center/.venv/bin/pip install -r control_center/requirements.txt
cd control_center/web
npm ci
npm run build
```

After backend edits, let any active workflow finish before restarting the server.

Geography uses full state/province and country names (for example,
`New York, New York, United States`). Region aliases are scoped to their country;
unknown or ambiguous locations are preserved rather than inferred. To preview a
backfill, run `control_center/.venv/bin/python -m control_center.manage normalize-geography`;
add `--apply` to save it, or `--week WEEK_ID` to limit it to one week. The backfill
creates a database backup, records old/new values, and refreshes current prepared
snapshots while preserving original evidence and historical review inputs.

## Two Workflows

The app starts without private historical inputs. Real collection and publication
require Google OAuth setup, and model calls require an authenticated Codex CLI.
Historical `benchmark/` archives and portfolio snapshots are optional; missing
archives import nothing and missing portfolio data disables exact portfolio
matching. No credentials-free sample dataset is included. See the repository
[publication-readiness audit](../docs/publication-readiness.md) before publishing.

1. **Prepare Data**, outside the demo timer: collect the configured Gmail Deals
   newsletters, enumerate every source item, extract every completed financing,
   consolidate duplicates, research official sites and announcements, normalize
   fields, document genuinely unavailable values, and reconcile duplicates again
  after research. Ten frozen historical weeks are imported as raw sources, not
  as ready-made classifications. Preparing one week does not prepare all ten.
   Research retains actual customer/use-case and market context, not just a
   homepage tagline. Geography stays a company location; market reach remains
   in source evidence. Routine field accuracy and corrections are checked in processing before freezing the snapshot. Older evidence contracts are refreshed on preparation.
2. **Review & Publish**, inside the timer: make fresh mandate decisions on every
   active financing with two blind reviewers running in parallel. Accept supported
   agreements; adjudicate disagreements, evidence gaps, and concrete unresolved mandate criteria. Research concrete missing facts when necessary,
   resolve final Yes/No, protect verified portfolio exclusions, validate the final package, publish the identical selected set to
   Sheet/Stats/Doc, then read back the values and formatting.

## Corrections

Sector uses the 20 StartHere industry labels, with one primary sector per deal.
Specific customer industries take precedence over generic technology labels when
equally supported. Sector describes the business; evidence-based mandate Fit is
separate. The taxonomy version is recorded in preparation and review provenance.
Current records and prepared snapshots use the new labels; historical review
records and decisions retain their original values. Sector migration creates a
database backup and records each old/new value and its classification basis.

The checkbox week selector is used in Control Center, Run History, and Deal
Database. Click anywhere on a week row to toggle it. The selector shows a date
range for one week or a selected count for multiple weeks. Choose multiple weeks
to prepare only the unprepared inputs, see combined deduplicated counts, and Review & Publish the
same selected dataset in one operation. Combined publications replace only the
selected weeks, show one Stats row per selected week plus combined totals, and
identify those weeks in the Doc. Each combined run preserves its selected input
snapshots and exchange-rate sources for historical inspection.

- Remaining Missing Fields counts missing cells that still need attention in
  current normalized records, not the original preparation report. Open the count
  to repair a field or mark it addressed with the circle icon. Show addressed in
  the table header reveals muted rows with checked-circle icons; click an icon
  again to reopen its field. Acknowledgments persist without changing field values
  or publication completeness checks.
- Edit at the top of the company panel makes its field rows directly editable.
  Fields are normalized and validated before saving; change notes and source
  URLs are optional. Save records changes in Correction History; Cancel discards
  unsaved edits.
- Review Decision accepts final Yes/No with optional reasoning and an existing
  evidence item or source URL. First-pass and independent answers are never
  rewritten; disagreement filters continue using those answers.
- Each completed review retains its input records and original reviewed baseline.
  Corrections update a working record, the next prepared snapshot, local metrics,
  and a traceable correction history. Financing-specific edits are scoped to the
  record, not automatically propagated to later rounds or other companies.
- Fit-affecting edits require explicit decision confirmation or Recheck Company.
  A targeted check uses supplied evidence and preserves explicit human overrides.
  It does not perform another web search. New source URLs are user-supplied
  evidence, not automatically verified sources.
- Refresh Outputs publishes the corrected package without rerunning the full
  review. Unconfirmed fit changes block refresh. A partial Google failure leaves
  outputs pending and retryable; original run timing remains unchanged.
- Saving a decision in the Decisions table queues an automatic Google output
  refresh for that deal. It runs after required confirmations/rechecks complete,
  using the review's existing destination and consistent Sheet/Stats/Doc package.
  Other unpublished edits must also be explicitly queued from the table or
  published with Refresh Outputs. Local-only reviews stay local. If publication
  fails, the decision remains saved and Refresh Outputs provides an explicit
  retry; no automatic retry or sign-in is attempted. Company-panel edits retain
  the manual refresh behavior unless resolving an already queued table edit.
- Source repair rejects tracking/reporting domains as company identity. A dry-run
  audit can be followed by an applied reconstruction from original extractions:

```sh
control_center/.venv/bin/python -m control_center.manage repair-sources --week WEEK_ID
control_center/.venv/bin/python -m control_center.manage repair-sources --week WEEK_ID --apply
```

Back up the local database before applying a repair. Source repair invalidates the
old review's preparation version; review the repaired dataset before publishing.
Original extracted content and past review decisions remain available.

Storage defaults to `control_center/data/`. An alternative local workspace can
set `DEAL_DATA_DIR`; credentials remain in the normal local secrets directory.
The default app does not require an additional workspace integration.

Run History opens a separate read-only Run Details view. Results, audit events,
timings, saved rows, and company snapshots belong to the selected run. Original
reviewed baselines take precedence over later edits; later correction events are
shown separately. Missing historical company snapshots never fall back to the
current dataset. View Current Week returns to the editable workspace. Google
links are labeled Live outputs because later publications can change those files.

To preview a history reset, run
`control_center/.venv/bin/python -m control_center.manage clear-history`.
Add `--apply` to back up the database and remove run-owned history records;
prepared deals, source inputs, field corrections, and feedback are preserved.
An active workflow blocks the reset. Optional `--week WEEK_ID` limits the reset
to that week. Local snapshot and artifact files remain available on disk.

New reviews use compact binary Yes/No outputs from both reviewers, without
confidence scores or confidence intervals. Agreement alone is insufficient when
decisive facts or mandate interpretation are uncertain. The adjudicator resolves
those cases against the original evidence and can request targeted live research.
Only final Yes rows are published. Both original answers, routing reasons,
adjudication and research citations are saved with the run. The Decisions selector
shows All or Disagreements, based on the original reviewer answers even after
resolution or a human correction. Older runs retain their original workflow and
subjective confidence displays. No real CRM is connected, so an unmatched
portfolio entry does not mean the fund never saw the company.

The workflow is isolated in the `review-workflow-adjudicator` Git checkpoint.
Its parent, tagged `review-workflow-two-reviewers`, retains the previous first-pass
plus blinded independent-check flow with the clarified mandate, sector taxonomy
and database count display. To restore that workflow without resetting history:

```sh
git revert review-workflow-adjudicator
npm --prefix control_center/web run build
```

Then restart the idle local server as described above. Reverting source code does
not delete saved runs, prepared data, or human corrections in the local database.

Source counts are per newsletter. The September 16-22 historical week contains
five emails from each source, fifteen total, not five emails across all sources.
Missing newsletter coverage is visible and must not be described as complete.

Source-context refreshes can be run separately without publishing or supplying
prior fit decisions. They start from the immutable prepared snapshot, preserve
newsletter dates, and create a new snapshot rather than changing the old one:

```sh
control_center/.venv/bin/python -m control_center.manage enrich \
  --week 2026-09-16_2026-09-22 --target local
```

An optional repeated `--company "Exact Name"` limits a diagnostic refresh.
Targeted diagnostic repair is not evidence of full-corpus accuracy.

If preparation fails during web research, use its recorded run ID to resume.
Completed outputs are schema-, source-, and tool-contract checked before reuse;
uncompleted or mismatched work is researched again. The original run stays failed.
No previous fit decision is used. New research uses smaller three-company batches,
and queued jobs are cancelled when a batch fails:

```sh
control_center/.venv/bin/python -m control_center.manage resume \
  --week 2026-08-05_2026-08-11 --from-run FAILED_RUN_ID --target local
```

Preparation also freezes public Bank of Canada daily exchange rates for the
four-week Stats window. Existing prepared weeks can receive that cache without
repeating research or fit decisions:

```sh
control_center/.venv/bin/python -m control_center.manage rates \
  --week 2026-09-16_2026-09-22 --target local
```

## Google Authorization

The Sheets and Docs APIs must be enabled in the OAuth client project. The account
behind the local write token also needs Editor access to both target files.
API enablement does not grant file permissions. A browser or connector account
can differ from the OAuth account used by the application.
This deployment accepts only the account configured as `GOOGLE_ACCOUNT`.
Authorization verifies that email
before replacing a token; a different account is rejected. Write authorization
also requests OpenID/email identity so the account can be verified. Gmail remains
read-only. The private demo files must be owned by that account, not created under
another browser/connector account as a sharing workaround.

```sh
control_center/.venv/bin/python -m control_center.auth --reauthorize
control_center/.venv/bin/python -m control_center.auth --gmail
```

Write authorization has Sheets and Docs scopes. Gmail has a separate read-only
token. Complete warnings, credential entry, and permission screens yourself.
An authentication rejection persistently blocks the affected connection, including
connection checks and queued requests, without reopening sign-in. The block
survives server restarts and ordinary token-file replacement. Reconnect explicitly
with `--gmail` for Gmail or `--reauthorize` for Sheets/Docs; only successful account
verification and credential storage clear that connection's block. HTTP 401 does
not trigger automatic refresh retries. Temporary service errors and quota/file
permission errors do not themselves require reconnection. Block markers live beside
the local token files and contain no tokens or provider error payloads.
Credentials and workflow data are excluded from Git. Do not share `data/`,
newsletter bodies, portfolio files, or credential folders in a public demo repo.

## Publication Contract

- Test targets are configured in ignored `google.local.json`; production needs the explicit
  `DEAL_ALLOW_PRODUCTION=1` environment flag.
- A repeat publication replaces the chosen week's rows on the test Sheet,
  preserves other weeks, recalculates the four-week Stats range, and rewrites only
  the two deal sections and two metric value cells in the native roundup.
- Blurbs are 10pt normal text with bullets, bold company/amount/backers, official
  homepage and announcement links, and at most eight named backers plus
  "and others". The Sheet retains all backers.
- A fresh Doc revision prevents overwriting concurrent edits. Protected native
  elements in the deal replacement range stop publication before deletion.
- The Sheet and Doc are separate Google transactions. A partial failure is
  recorded, not reported as success. Retry is week-replacement safe, but inspect
  the audit first. There is no cross-service transaction or automatic rollback.
- Linked chart references must point to the selected Sheet. Google Docs linked
  chart image refresh is manual and is NOT included in the measured pipeline
  time. This is disclosed in the interface and each run.
- Source-provided USD equivalents are used before FX. Other explicit currencies
  use the frozen [Bank of Canada daily rates](https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates/):
  same-day or latest prior business-day observations, at most seven calendar days
  old, using same-date CAD cross rates. Rate dates, source URL, conversion basis,
  and included USD amounts are retained in the run audit. No FX network call is
  made during live review/publication; the same frozen rates serve historical
  Stats rows and current rows. The Sheet amount is a dollar-only USD value;
  original currency, financing qualifiers, and conversions remain in the audit.
- Capital Announced counts newsletter-reported current VC round amounts,
  including anticipated rounds, initial closings, and conditional commitments.
  It is not verified cash closed. Valuation, cumulative/lifetime funding, pure
  debt/project financing, mixed debt/grant totals without a VC breakdown, and
  unavailable or unconvertible amounts remain excluded. The audit explains
  exclusions. Financing qualifiers remain in source evidence and qualified Doc
  wording rather than cluttering the Sheet amount cell. Selected-row completeness
  records every remaining unavailable field.
- When a named round is not disclosed, the Round column uses the reviewed
  Early/Growth stage rather than inventing a series. The audit preserves the
  undisclosed-round reason and the Doc describes a stage-level funding round.
  Named rounds normalize to Pre-seed, Seed, or Series A/B/etc.; extensions and
  numeric suffixes collapse to the base round. Pre-Series A uses the stage
  fallback rather than being mislabeled Series A.

## Audit And Feedback

SQLite stores raw sources, active and superseded financings, field evidence,
portfolio matches, first and independent decisions, final decisions, corrections,
timestamps, stage timings, and failure details. Every model job saves its prompt,
schema, response, events, and token usage under `data/artifacts/<run>/`.
Superseded duplicates are retained for historical audit, not silently deleted.
Each run freezes one mandate text for both review layers and records its SHA-256,
alongside the prepared-input, review-input, and workflow-code hashes.

Company details expose source excerpts, official-site evidence, missing-field
reasons, and fit correction memory. Source Evidence starts collapsed. Edit at the
top of the company panel enables its field rows and Review Decision in place;
Save records old/new values in Correction History. Notes and decision sources
are optional, and an unsourced edit is recorded as a user assertion. Similar
sector corrections are retrieved as guidance; newer evidence remains
authoritative. Current-review corrections rebuild the local package; corrections
after an input refresh are saved for the next review while preserving historical
results. Company-panel saves remain local; decision-table saves explicitly queue
automatic output refresh after the required checks, as described above.

## Timing, Models, And Costs

The server timer covers review trigger acceptance through Google write and
readback completion. The browser separately records click-to-visible-result time,
including request and polling latency. Local-only runs explicitly exclude Google
publication and are never end-to-end evidence.

Preparation runs use three workers. Review defaults to six workers with twenty-two
companies per batch. Reviewer A and B batches are submitted together to the same
bounded worker pool; neither receives the other answer. Their wall-time intervals
overlap, so the parallel-review duration must not be calculated by adding them.
Comparison, adjudication, targeted research and final resolution have separate
recorded wall times. Research is conditional and runs across companies in parallel.
At least two review workers are required so both blind reviewers can start together.
Repeated selected coverage of the same company and financing is merged before
output validation; separate rounds remain separate, and every original review
record and source stays in the audit.
`DEAL_REVIEW_SCHEDULE` applies only to the retained legacy comparison helper;
new Review & Publish runs always use the dual independent/adjudicator workflow.
All defaults currently use Sol; alternatives are selectable:

```sh
DEAL_REVIEW_MODEL=gpt-6-luna DEAL_VERIFY_MODEL=gpt-6-sol \
  control_center/.venv/bin/python -m control_center.server --port 8766
```

`DEAL_REVIEW_BATCH_SIZE`, `DEAL_REVIEW_WORKERS`, and `DEAL_EXTRACT_MODEL` are also
configurable. Faster settings are experimental until paired accuracy and full
publication runs substantiate them. Astra is not a required default dependency.

Models run through the signed-in Codex CLI. The audit records actual reported
token usage and web-tool calls, not an API invoice. A dollar estimate requires
applicable model prices and a clearly identified billing basis; this workflow
does not claim these tokens were separately billed at API rates. The Python
worker pool and local database require no paid orchestration service.

## Evaluation

```sh
control_center/.venv/bin/python -m unittest discover -s control_center/tests -v
```

The Evaluation view compares completed reviews with eighty existing historical
diagnostic labels. Those labels are partly post-hoc and disagreement-selected,
not a blind, representative test set. Unreviewed weeks are unscored. Relevant
companies missing from a completed week's extraction count as recall failures.

To prove the demo target, run multiple representative prepared weeks against
accessible native Google copies. Report decision accuracy/recall, selected-row
completeness, deterministic output agreement, median and worst observed elapsed
time, and any chart-refresh exclusion. Unit tests or a sub-two-minute local
review alone do not prove a viable end-to-end demo.
Google readback proves the published package matches across outputs, not that
every source fact or fit decision is correct. Post-run source failures remain in
the run history with their timing preserved and are not accuracy-passing demos.
