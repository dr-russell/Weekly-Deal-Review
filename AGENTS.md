# Deal Review Control Center

This repository contains the Control Center and its supporting agent pipeline.

## Structure

- App code, tests, and reference configuration live in `control_center/`.
- Keep each fund's mandate, sheet, document, feedback state, and scripts isolated.
- Do not overwrite one fund's config with another fund's mandate or output targets.

## Operating Notes

- Gmail OAuth secrets are local credentials. Do not commit or share `secrets/`.
- Local feedback JSON is fund-specific. Do not merge feedback between funds unless the user explicitly asks.
- For Market Deals refreshes, process user-reviewed `Fit? = No` feedback before removing rows from the active sheet.
- Publish reviewed `Fit? = Yes` rows.
- MetaProp: `Maybe` is internal routing only. Resolve every deal to a final `Yes` or `No` before publication; the completed sheet must contain no `Maybe` rows. Include evidence-backed borderline fits as `Yes`, not unresolved placeholders.
- Other local fund workflows retain their own review policy; do not change another fund's policy as part of a MetaProp update.
