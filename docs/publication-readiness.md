# Publication Readiness

Review date: October 5, 2026. This records the current local source review,
not approval to publish or a guarantee about external copies.

## Scope And Results

- Reviewed the current tracked-file inventory, source text, README, historical
  screenshot, and author/committer metadata in the active local history.
- The source tree contains the Control Center, supporting agent pipeline,
  reference configuration, tests, and documentation. Separate fund workflows
  remain local and are not tracked. The app no longer depends on their files.
- Targeted searches found no common private-key, Google API-key/OAuth-secret,
  GitHub-token, OpenAI-key, or AWS-key patterns, known personal account email,
  or actual Google Sheet/Doc output links in the current tracked text.
- No newsletter corpus, portfolio export, runtime database, evidence artifact,
  OAuth credentials, or token files are tracked. Local Google settings use
  ignored JSON files; the committed example contains placeholders.
- Active local commit authors and committers use a GitHub noreply email.
- All 67 backend tests and 6 frontend tests passed. They also passed in an
  exported source copy without private settings, reference assets, or separate
  fund workflows. This validates the test suite, not a full fresh-clone demo.
- A read-only Google snapshot succeeded after migrating app credentials into
  ignored `control_center/data/secrets/`. No Google output was modified by it.

This is a targeted review, not a comprehensive security audit or a guarantee
that every possible secret or confidential statement has been detected. The
current source checks do not prove removal from old commits, caches, clones,
forks, backups, or other external copies. Earlier repository checks found no
issues, pull requests, releases, Actions runs/artifacts, or commit comments.

## Remaining Publication Decisions

- Confirm ownership and permission to publish the code and any fund-specific
  mandate material, especially content derived from work.
- Decide whether to retain fund branding in the app, application README,
  mandate, and screenshot. The root README text is fund-neutral and its former
  firm-specific disclaimer has been removed; the embedded screenshot still
  displays fund branding.
- Create the replacement GitHub repository from the finalized tracked files
  with one fresh initial commit. Do not push old history or use a mirror push.
  Rewriting an existing remote can leave old commits accessible by hash.
- Verify the replacement's files and commit metadata before changing its
  visibility. Local Google settings, credentials, runtime data, and private
  history backups must not be force-added to Git.
- A license is optional for a public showcase. Add one only after deciding
  ownership and the reuse permissions to grant; public visibility alone does
  not supply an open-source license.

Google file-sharing settings are separate from repository visibility and have
not been changed by this cleanup. Link-accessible outputs are acceptable only
when their contents are intended for that audience. Sharing settings still
need review before publicly displaying or linking to live Google outputs.

## Screenshot

`docs/images/control-center.png` is a historical local demo screenshot. It shows
source counts, run status, aggregate decisions, and timing, but no account email,
Google file identifiers, company-level portfolio matches, or newsletter bodies.
It is not a new benchmark and predates the Capital Announced policy; its README
caption discloses both the timing exclusions and that policy difference.

## Runtime Limits

The application is local-only; no public deployment is included. A fresh clone
still needs private portfolio/historical reference assets, Google OAuth setup,
local target settings, and an authenticated Codex CLI to run the full workflow.
There is no credentials-free sample dataset. These limitations are disclosed
in the README and do not prevent publishing the source as a portfolio project.

## Status

At pre-publication review, both previous repository URLs returned HTTP 404 through the
authenticated GitHub API. This does not certify deletion of every external
copy or cached object. On October 5, 2026, the owner explicitly authorized
creating a fresh public replacement from the finalized tracked files with one
initial commit. Verify its source tree, commit metadata, and visibility as part
of publication. This authorization is not an independent determination of
intellectual-property rights. Google sharing remains unchanged.
