# Codexio product context

<!-- impeccable:product-schema 1 -->

## Platform

Windows 10/11 desktop, implemented with Python and PySide6. Browser artifacts in build/ui-design-review are isolated design previews, not a platform migration.

## Users

Confirmed on 2026-09-08: the primary user frequently opens the main application to analyze usage and requests. The floating quota window is also an existing product surface.

## Product Purpose

Read Codex account rate limits and locally indexed usage records so the user can understand remaining quota, token consumption, API-equivalent cost, and individual requests.

Confirmed on 2026-09-10 for local 0.2.2 development: Logs page one automatically selects and previews its first available record. Today with no records keeps the empty state; background refresh preserves the user's selected record or explicit dismissal. Complete each change with verification, packaging, and a local Git commit. Pushing and remote releases require new explicit authorization.

## Capabilities and Constraints

Repository evidence: five-hour and weekly remaining quota and reset times; read-only available reset-credit counts; usage trends and history; grouping by user request or model call; model pricing; local and opt-in SSH sources; floating and docked quota windows; settings; light and dark main-window themes.

Dollar amounts follow the user-approved definition: Standard API base prices multiplied by Codex model/context/speed rules. The same derived catalog prices calls, request groups, overview metrics, trends, floating summaries and weekly estimates. These amounts are not actual API invoices, subscription charges or official credits. Weekly estimates represent observed usage and may be unavailable. Unknown and stale values must not be presented as zero or current data.

The user explicitly authorized native UI implementation on 2026-09-08 after reviewing C. Implement the current C design, keep the overview first while allowing persisted navigation reordering, and keep each Token composition label next to its percentage. Preserve the current application version. Local build and verification are part of delivery; a public release is not requested.

## Brand Commitments

Use the user-approved Codexio name and Quantum "X" Core icon: dark rounded container, white code brackets, and a warm amber X. Preserve the approved C workspace. The user's latest references call for brighter pastel data colors: coral price, mint Token, and purple/blue/yellow Token composition with matching legends. Preserve native typography and paired Token labels. The user explicitly wants the two lines in one plot, using additional Token-axis headroom to keep Token generally lower while the price scale stays normal; genuine overlap is allowed.

Overview quota bars use the same weight as Token composition and transition from pastel green through yellow to red as quota falls. Metric cards keep their values and period comparisons without bottom annotation lines. Chart points stay visible, and tooltip metrics lead with price.

Compare against complete previous units: yesterday in full, or the complete seven/thirty days preceding the selected range. Date controls must show the full native calendar. Subscription current data and cycle history form one continuous page, without a mode switch.

Reset dates include a Chinese weekday throughout the main window and floating styles. Subscription gauges have larger arcs, thicker strokes and larger text. Weekly projection starts at a 2 percentage-point change and accepts valid reference prices with an explicit reference-estimate status; missing prices remain unavailable. Omit the explanatory prose beside the estimate. Move page and preview scrollbars toward their right edges, and keep the preview session title visibly larger than the message body in both themes.

The sidebar now uses six two-character labels: 概览、日志、用量、订阅、定价、设置. Native Windows caption colors follow the selected app theme. The earlier Chinese-led Astra brand proposals were rejected. Current branding requirements are English-only names connected to AI, Quota, Widget or Codex, with original marks related to ChatGPT/Codex visual language. The user selected Codexio and supplied exact Quantum "X" Core SVG artwork on 2026-09-09. The earlier proposal directories are historical references. Current brand resources are in docs/branding/codexio.

## Evidence on Hand

Confirmed on 2026-09-10: retain only ordinary-context Standard API rates as base data, derive Codex modifiers centrally, and keep base and derived rows together in one pricing table without separate section headings. Manual edits change only the base; a price-policy revision automatically revalues history and dependent caches.

README.md, src/codexio/dashboard.py, src/codexio/window.py, src/codexio/theme.py, and current synthetic screenshots rendered with scripts/render_preview.py. Preview data must be visibly identified as synthetic.

## Product Principles

- Support sustained analysis with consistent filters, clear information hierarchy, and inspectable request details.
- Keep quota status and data freshness easy to find while analyzing usage.
- Preserve existing data semantics and do not invent new telemetry or billing claims.
- Design artifacts remain proposals until explicitly accepted.

## Open Decisions

The current C prototype and C-REFINEMENT.md define the approved structure. Refinements use a permanent request-details pane, remove decorative estimate badges near API prices, and add a source-grounded daily Token calendar. All usage date selectors default to Today; the overview quota/quota/Token row uses equal thirds, and its API cost and Token lines share one chart with explicit separate scales. Request details stay in a permanent right pane with an empty selection hint; row clicks show content, while pointer movement and outside clicks leave the selection intact. The left table scrolls horizontally at narrow widths. Running status is vivid green and bold, quota bars use brighter mint green at 75–100%, and pricing headers and cells are centered in six uniformly sized columns. Their fixed heading is the session title, followed by up to three actual message lines, an additional image-count line, usage summary, reply, Turn/Session IDs, and model-level call counts with summed costs, without member pagination. Plugin mentions use @Name; generated context must not replace actual user text. Default navigation is Overview, Logs, Trends, Subscription, Pricing, Settings, with subsequent custom ordering preserved. Logs share a rounded canvas without the technical aggregation footer. Source columns default hidden with a saved Appearance setting. The user authorized rebuilding and replacing dist/Codexio.exe and dist/latest.json on 2026-09-09; these refinements are packaged locally at version 0.2.1. A public release was not requested. Further version changes require explicit confirmation.
