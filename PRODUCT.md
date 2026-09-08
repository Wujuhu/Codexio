# AIQuota product context

<!-- impeccable:product-schema 1 -->

## Platform

Windows 10/11 desktop, implemented with Python and PySide6. Browser artifacts in build/ui-design-review are isolated design previews, not a platform migration.

## Users

Confirmed on 2026-09-08: the primary user frequently opens the main application to analyze usage and requests. The floating quota window is also an existing product surface.

## Product Purpose

Read Codex account rate limits and locally indexed usage records so the user can understand remaining quota, token consumption, API-equivalent cost, and individual requests.

## Capabilities and Constraints

Repository evidence: five-hour and weekly remaining quota and reset times; read-only available reset-credit counts; usage trends and history; grouping by user request or model call; model pricing; local and opt-in SSH sources; floating and docked quota windows; settings; light and dark main-window themes.

API-equivalent dollar amounts are estimates using model prices, not subscription charges. Weekly estimates represent observed usage and may be unavailable. Unknown and stale values must not be presented as zero or current data.

Confirmed review boundary: design only until the user explicitly authorizes changes to application source. Choosing a design direction is not permission to implement. Do not build, publish, or change the application version during design review.

## Brand Commitments

Keep the AIQuota name. The user requests a distinctive, refined interface and authorizes layout and interaction redesign. The current appearance is not binding. No replacement logo, palette, or typography has been approved.

## Evidence on Hand

README.md, src/aiquota/dashboard.py, src/aiquota/window.py, src/aiquota/theme.py, and current synthetic screenshots rendered with scripts/render_preview.py. Preview data must be visibly identified as synthetic.

## Product Principles

- Support sustained analysis with consistent filters, clear information hierarchy, and inspectable request details.
- Keep quota status and data freshness easy to find while analyzing usage.
- Preserve existing data semantics and do not invent new telemetry or billing claims.
- Design artifacts remain proposals until explicitly accepted.

## Open Decisions

Visual direction and specific interaction changes await review. The user's preferred review format is pending; no standing design-workflow default has been selected.
