---
name: Codexio
description: A focused native workspace for quota and usage analysis.
colors: {
  dark-bg: "#101012", light-bg: "#FCFCFE", dark-surface: "#1B1B1F", light-surface: "#F1F1F5",
  dark-raised: "#303035", light-raised: "#E2E2EA", dark-text: "#F0F0F3", light-text: "#25252B",
  dark-inspector-surface: "#292B33", light-inspector-surface: "#FFFFFF", dark-inspector-border: "#545866", light-inspector-border: "#B9BDCA",
  dark-muted: "#AFB0BA", light-muted: "#565661", dark-border: "#35353D", light-border: "#D1D1DB",
  dark-control-border: "#44444D", light-control-border: "#BCBCCA", dark-accent: "#DDDDE5", light-accent: "#41414C",
  dark-action: "#DDDDE5", light-action: "#35353F", dark-action-hover: "#F0F0F5", light-action-hover: "#50505E",
  dark-action-text: "#1B1B20", light-action-text: "#FFFFFF", dark-selection: "#2A2A32", light-selection: "#DFDFEA",
  dark-hover: "#292930", light-hover: "#E9E9F0", dark-sidebar: "#202022", light-sidebar: "#EDEDF1",
  dark-success: "#83BEA0", light-success: "#2F6348", dark-running: "#58E8A0", light-running: "#00733A", dark-warning: "#E5B782", light-warning: "#87550F",
  dark-grid: "#303037", light-grid: "#D5D5DF", dark-chart-tokens: "#8CDACC", light-chart-tokens: "#65BFAE",
  dark-chart-cost: "#FFA18E", light-chart-cost: "#F07158", dark-chart-input: "#65D0F7", light-chart-input: "#45BCEB",
  dark-chart-cache-read: "#BEA7FF", light-chart-cache-read: "#AB8DED", dark-chart-cache-write: "#F0A9CA", light-chart-cache-write: "#DB8AB5",
  dark-chart-output: "#F8D36A", light-chart-output: "#EDBE4C",
  dark-token-cache-read: "#BEA7FF", light-token-cache-read: "#BCA2FF", dark-token-input: "#56CCF3", light-token-input: "#49C9F4",
  dark-token-output: "#F8D36A", light-token-output: "#FFD14F", dark-token-cache-write: "#F0A9CA", light-token-cache-write: "#F0A4C8",
  dark-comparison-up: "#72D6A4", light-comparison-up: "#127B54", dark-comparison-down: "#F3A1A8", light-comparison-down: "#B64B54",
  dark-quota-high: "#71ECAE", light-quota-high: "#69E6A6", dark-quota-mid: "#F8D879", light-quota-mid: "#F7D577", dark-quota-low: "#F2A1A6", light-quota-low: "#F19BA0",
  dark-quota-progress: "#82A8CA", light-quota-progress: "#47749A", dark-activity-empty: "#282B2B", light-activity-empty: "#E7EBE8",
  dark-activity-1: "#254438", light-activity-1: "#CCDFD0", dark-activity-2: "#3B6651", light-activity-2: "#A0C4A9",
  dark-activity-3: "#5B9273", light-activity-3: "#6B9D7A", dark-activity-4: "#8AB99B", light-activity-4: "#3A7251"
}
typography:
  display: {fontFamily: "Microsoft YaHei UI, Segoe UI", fontSize: "29px", fontWeight: 500}
  headline: {fontFamily: "Microsoft YaHei UI, Segoe UI", fontSize: "22px", fontWeight: 600}
  title: {fontFamily: "Microsoft YaHei UI, Segoe UI", fontSize: "16px", fontWeight: 600}
  body: {fontFamily: "Microsoft YaHei UI, Segoe UI", fontSize: "13px"}
  ledger: {fontFamily: "Microsoft YaHei UI, Segoe UI", fontSize: "12px"}
  label: {fontFamily: "Microsoft YaHei UI, Segoe UI", fontSize: "11px"}
rounded: {input: "7px", control: "8px", tooltip: "10px", panel: "13px", card: "14px", canvas: "18px"}
spacing: {pair: "6px", xs: "8px", sm: "12px", md: "16px", section: "18px", lg: "22px", canvas: "26px"}
components:
  button-primary: {"backgroundColor":"{colors.dark-action}","textColor":"{colors.dark-action-text}","rounded":"{rounded.control}","padding":"7px 11px"}
  button-secondary: {"backgroundColor":"{colors.dark-surface}","textColor":"{colors.dark-text}","rounded":"{rounded.control}","padding":"7px 11px"}
  button-quiet: {"backgroundColor":"transparent","textColor":"{colors.dark-muted}","rounded":"{rounded.control}","padding":"6px 9px"}
  input: {"backgroundColor":"{colors.dark-surface}","textColor":"{colors.dark-text}","rounded":"{rounded.input}","padding":"7px 10px"}
  navigation-selected: {"backgroundColor":"{colors.dark-raised}","textColor":"{colors.dark-text}","rounded":"{rounded.control}","padding":"7px 10px","height":"39px"}
  segment-selected: {"backgroundColor":"{colors.dark-raised}","textColor":"{colors.dark-text}","rounded":"{rounded.control}","padding":"5px 10px"}
  card: {"backgroundColor":"{colors.dark-surface}","textColor":"{colors.dark-text}","rounded":"{rounded.card}","padding":"20px 22px"}
  quota-meter: {"backgroundColor":"{colors.dark-surface}","textColor":"{colors.dark-text}","rounded":"{rounded.panel}"}
  token-composition: {"backgroundColor":"{colors.dark-surface}","textColor":"{colors.dark-text}","rounded":"{rounded.panel}"}
  request-ledger: {"backgroundColor":"{colors.dark-bg}","textColor":"{colors.dark-text}","typography":"{typography.ledger}"}
---

# Design System: Codexio

## Overview

**Creative North Star: "C · Quiet analytical workspace"**
C is a quiet analytical workspace for sustained desktop use. An inset canvas and full-height navigation frame the data; compact quota summaries, measured typography, restrained chart colors, and daily activity keep the next comparison easy to find. This is the implemented Windows/PySide6 visual system.
**Key Characteristics:**
- A continuous desktop workspace with a persistent sidebar.
- Tonal surfaces, compact controls, and inspectable records.

## Colors

**Primary:** pale ink and charcoal identify actions; blue carries quota progress. **Data:** follow the user's pastel references: coral price lines, mint Token lines, lavender cache, cyan input, yellow output, and pink cache writing. Keep text legible by using darker ink variants for light-theme axis titles and tooltips. Green/up and red/down identify percentage direction with arrows and signs. Preserve status and low-quota meaning. **Neutral:** keep the dark shell and cool paper light shell while giving the data clear, bright color.
Component tokens show the dark variant; main-window components bind the corresponding light tokens in light mode. Keep secondary text and colored status readable on selected rows as well as at rest. The floating window and its actual settings preview retain their own dark renderer and quota-status palette.

## Typography

Use the installed Windows font stack for Chinese and Latin text. Display is for summary values; headline for page and profile headings; title for section headings; body for controls; ledger for primary row content; label for secondary row text, captions, and freshness. Main ledger lines are separated by a measured gap (5 logical px); half-ring values use medium type (34 logical px). The inspector session heading uses 20 px semibold, explicitly styled so the global body rule cannot flatten its hierarchy on theme changes.

## Layout

All measurements are Qt logical pixels. The window starts at (1280 × 850), supports a minimum (920 × 660), and keeps a full sidebar (180). The canvas is inset at top, right, and bottom (10), with outer content margins (26, 22, 8, 12). Headers, footers and scrolling-page content retain 18 px of inner right spacing. Page scrollbars sit 9 px from the canvas border; table cards and the inspector use 4 px right insets, placing the inspector scrollbar 5 px from its border without crowding its text.
Overview gives the five-hour quota, weekly quota, and Token composition equal thirds of the row after gaps; whole label–percentage groups wrap when needed. Three summary metrics lead into a combined API/Token trend and recent-request ledger. Overview, Trends, and Logs default to Today, with hourly chart buckets; explicit selections and drilldowns still take precedence. Inner content scrolls while the shell remains stable. Trends scroll internally, with the chart bounded (380–500 high) and daily activity below.
Subscription uses one scrolling page: current profile, quota and reset credits first, then the cycle-history summary and table. There is no Current/History switch or nested page scroll area. Settings use a separate category list (118) and bounded fields. Date ranges use aligned 160 × 34 logical-pixel fields; their native calendar popup is 336 × 304, with six complete week rows and styling isolated from ledger padding.
The Logs table and pagination share one surface card with aligned content edges; filters remain above, and no technical aggregation footer is shown. Its header and table body use the card surface rather than the page ground. Ledgers preserve measured numeric widths through horizontal scrolling. The right details pane remains beside the table at every supported width (260–320 wide, with a 20 px gap); the left table scrolls horizontally when needed. Empty and selected states keep identical geometry. Keyboard activation shows details; Escape clears the selection while leaving the pane in place.

## Elevation & Depth

Main-window depth comes from tonal layering and fine borders, without card shadows. Each enabled chart series has a restrained fill of its own hue, confined to the data plot. Floating styles retain their existing text-shadow and percentage animation; the settings preview uses the same renderer and settles animations when hidden.

## Shapes

Use the frontmatter radius hierarchy: the canvas is the broadest enclosure, cards and painted panels are softly rounded, and controls are tighter. Ledgers remain flat with horizontal dividers; quota tracks have rounded caps. Chevron assets remain distinct at 100% and 150% Windows scaling.

## Components

- **Native window chrome:** synchronize the Windows caption dark-mode flag, background and text with the app theme. Use the sidebar ground and primary text colors; reapply after native-handle creation and window show. Keep custom floating windows and popup menus outside this treatment. Unsupported optional DWM attributes do not interrupt the app.
- **Controls and navigation:** primary, bordered, and quiet buttons retain hover, pressed, disabled, and visible focus states. Start in Overview → Logs → Trends → Subscription → Pricing → Settings order, adopting it once for legacy preferences. Sidebar labels are the concise Chinese 概览、日志、用量、订阅、定价、设置; full page headings stay unchanged. Selected navigation uses a raised neutral fill; subsequent drag, context-menu, or Alt+Up/Down reordering persists, with Overview pinned first.
- **Quota and Token summaries:** overview quota bars match the Token bar's 11 px height and rounded ends. Interpolate the fill color continuously between pastel red at 0%, yellow at 50%, and bright mint green at 75–100% remaining; missing quota stays a neutral empty track. Keep the percentage and reset text explicit. Subscription retains blue half-ring gauges and its low-quota warning ink. Token composition uses an 11 px rounded segmented bar with 4 px gaps and matching 8 px legend dots. Label–percentage groups use 12 px type, retain the pair spacing token, and wrap whole groups. Segment widths preserve the recorded proportions after accounting for gaps. Include cache writing only when present.
- **Subscription:** keep profile, gauges, and per-credit rows distinct, followed immediately by cycle history in the same scrolling page. Remove the old mode selector, jump button and saved mode state. Each credit shows its own count, deadline date/time on separate lines, and remaining time; “截止时间未提供” and explicit “无到期限制” are separate states. Keep cycle reset dates on two lines and allow table-level horizontal scrolling at narrow widths so full labels remain available.
- **Request ledgers:** center headers and both row lines. Draw running status with vivid green and 13 px bold type, keeping at least 4.5:1 contrast on ordinary, hovered and selected rows. Read item fonts through the initialized delegate option. Lead with that record's real input preview, filtering generated context blocks before truncation; simplify plugin links to @Name and reserve a trailing [image] x N count without double-counting native/XML representations. Fall back to the recorded session title or identity only with an explicit label. Model and tier remain separate; Mixed is a request aggregation state and is disabled in model-call filtering. Hide the Source column by default; Appearance settings can enable it for both log modes without changing source data or filtering.
- **Inspector:** keep the pane permanently visible with a Request Details heading and a click-to-select hint when empty. On page one, select and preview the first available record without moving keyboard focus; a restored selection takes precedence. An empty Today view stays empty until records arrive. Click another row or activate it with the keyboard to switch details. Pointer motion and outside clicks never switch or hide content. Keep the selected table ID and scroll through refresh and filtering while it remains on the current page; otherwise select the first available page-one row or return to the hint on later pages. Escape clears the selection without reopening it on background refresh; a new filter or page change restores the default. When selected, the fixed heading is the real session title, with no repeated request heading or small session label. Below it, one scroll area contains the bold request, API/usage summary, reply, Turn/Session IDs, then model-level call composition. The request uses only its actual one to three lines; image counts add a separate fourth line when needed. Missing requests use an explicit empty state. Separate these groups by 20 logical px and keep related labels 8–12 px apart. Collapse all calls of the same model into model × count and the sum of actual call costs, combining tiers as Mixed and preserving unknown-price states. Do not paginate or route an aggregate to an arbitrary individual call. Use the inspector surface/border tokens to distinguish the preview from its ledger: raised cool charcoal in dark mode and white in light mode. Escape clears the selection only while Logs is active; the pane stays visible. Keep toolbar/sidebar actions and inner reply selection/ID copying usable, with no application-wide dismissal filter or hover timer.
- **Charts:** overview shows API cost and Tokens in the same plot with a Token left axis and USD right axis. Use crisp 2 px coral/mint lines, faint horizontal grids and very light fills of the corresponding hues. Draw every known point without hover, using a surface-colored outline; dense histories use smaller points. Hover enlarges the current points and adds the guide line. The tooltip keeps its date heading, then lists Price, Total Token, and call count before optional components. Keep both axes zero-based. At the user's request, the combined view gives the Token axis 2.2× peak headroom and the price axis its normal 1.12× headroom, so Token usually sits lower; tick labels reflect those exact scales and genuine crossings remain possible. A Token-only view uses normal 1.12× headroom. Never bridge unknown-price gaps; hover and bucket navigation retain actual values.
- **Period comparisons:** overview metric cards contain the title, value and directional percentage, without the former bottom annotation line. Keep supporting counts and missing-price detail in metric tooltips. Also show comparisons in three cards below the Trends chart. The baseline is a complete preceding local-time unit: all of yesterday, or the full seven/thirty days immediately before the selected range starts. Current values retain the selected range through now; do not truncate the baseline to the same clock time. Model filtering applies to both windows. Filter each metric independently in each period and use the comparison's current totals for the card values. Unknown prices do not discard confirmed Tokens or independent calls. Do not count cumulative observations/deltas as independent calls without a confirmed response identity. Missing valid data stays null and displays “暂无对比” with “—” for the unknown card value; a known zero baseline with a nonzero current value displays “前期为 0”, and two known zero totals display 0.0%. Explain skipped records with “按已确认数据计算” in the tooltip. Incomplete source sync does not block these comparisons; ledger, diagnostics and estimation completeness guards stay intact. Keep unknown chart metrics as gaps and validate malformed counters before drawing. Query only bounded dates on visible-page refresh and reuse the result within the current data revision and minute.
- **Date picker:** share one native date control across Trends, Logs and history attribution. Reset nested editor borders, calendar table-cell padding and navigation button metrics; include all six weeks at 100% and 150% scaling. Preserve native month navigation, keyboard selection and dateChanged behavior, and remap the calendar colors and chevrons with the current theme.
- **Daily activity:** use a GitHub-like, seven-row Monday-first calendar for a rolling 365 local days of Tokens, with an explicit range, recorded-day count, totals, and four positive intensity levels. Hover shows day Tokens, calls, cost, or “暂无记录”; click or keyboard Enter opens that day's model-call logs with the current model. Cache by data revision/model/local day and query only while Trends is visible.
- **Pricing table:** keep one six-column table with centered headers and cells. Show only Standard, Fast and applicable context conditions; omit API Base reference rows. Bold all six cells of the ordinary Standard row and use the official OpenAI API standard rates, including a separately published cache-write rate. Fast and long-context rows retain existing Codex rules. Show conditions on two readable lines, using 50 px rows and 4 px / 8 px cell padding. Display all dollar amounts and unit prices to exactly two decimals across the application, including tooltips, compact axes, floating summaries and editors. Preserve underlying precision when calculating totals or saving untouched editor fields. Selecting any row identifies its model and condition; editing opens that model's Standard base, and filtering cannot redirect an edit to another model. All displayed prices and actual usage costs share the backend catalog. Missing prices remain unknown. All six columns share available width, with minimum widths and horizontal scrolling when necessary.
- **Main wordmark:** preserve the 32 px app icon. Use Times New Roman at 18 px and weight 600 for Codexio, with 4 px top/side padding and no bottom padding; this moves the lettering left and down from its previous position without changing the sidebar layout.
- **Settings:** keep Appearance, Floating Window, Data Sources, and Application separate. Preview the selected floating style with actual quota state and draft appearance; saving applies settings through the existing application callbacks.
- **Reset dates and estimation:** show the Chinese weekday after the local reset date in overview, subscription, history and every floating style, including per-credit deadlines. Wrap metadata in narrow floating styles and reserve its measured height. Subscription gauges use up to a 200 px diameter, 9 px strokes, 34 px values, 13 px headings and 12 px remaining/reset labels. Start weekly projection after a 2 percentage-point change; valid reference prices participate with a “参考估值” status, while missing prices still prevent projection. Remove the prose to the right of the estimate summary.

## Do's and Don'ts

- **Do** preserve label–percentage pairs, complete numeric values, independent record fields, and visible freshness or missing-data states.
- **Do** keep subscription profiles user-entered and blank by default; label synthetic previews and retain API-equivalent units without “估算” badges. Internal estimated-price flags remain intact.
- **Do** retain Qt keyboard focus, Ctrl+K search, scrolling, scaling, and the existing background-worker lifecycle.
- **Don't** infer renewal dates, credit deadlines, zero usage, Standard tier, or per-call timing from absent data.
- **Don't** replace the full sidebar with a narrow icon rail or make the floating surface follow the main-window theme.
