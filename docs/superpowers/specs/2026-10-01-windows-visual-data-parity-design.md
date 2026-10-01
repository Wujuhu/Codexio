# Windows Visual and Data Parity Design

## Goal

Bring the Windows 0.3.4 presentation and behavior into line with the current Mac implementation and the supplied ChatGPT references, while preserving Windows-native interaction, the existing Go/Wails architecture, current data definitions, and the fixed minimal verification workflow.

## Scope and constraints

- Keep `windows/VERSION` at `0.3.4`.
- Modify Windows code and Windows build inputs only. Mac, iOS, and frozen historical releases remain unchanged.
- Do not add a test suite, pytest, UI traversal, screenshot matrix, or Computer Use verification.
- Verify with frontend diagnostics, production compilation, the existing three isolated smoke checks, artifact hashes, and narrowly scoped data diagnostics where needed.
- Do not terminate or restart the user's running Codexio or ChatGPT application during development or verification.
- Do not push, publish a GitHub Release, or modify remote state.

## Shared typography and numeric presentation

Bundle `InterVariable.woff2` from the user-provided Inter 4.1 archive in the Windows frontend. An `@font-face` declaration supplies Inter for Latin text, Latin punctuation, and numerals. Chinese text falls through to the normal Windows Chinese interface font, with `Microsoft YaHei UI` and `system-ui` as the explicit fallback chain. No machine-wide font installation is required.

Replace locale-dependent compact notation with one deterministic application formatter. It emits English suffixes `K`, `M`, and `B`, retains a useful decimal for values below the next unit, and never emits Chinese `万` or `亿`. Overview metrics, trend scales, log statistics, model shares, chat ranking, subscription values, and floating-window summaries use this formatter. Daily, weekly, and monthly AI report rendering retains its report-specific Chinese notation.

The overview and trends cost metric shows only its numeric value. Partial or missing pricing remains available in the existing details, tooltips, and explicit status surfaces; it is not repeated as a small annotation below the metric.

## Trend continuity and unknown values

The chart projection distinguishes three states:

1. A time bucket with no calls is a known zero bucket. Token, cost, and user-request values are zero, and the line remains connected along the baseline.
2. A bucket containing calls with known Token but incomplete pricing retains its Token and request values while cost is `null`. Only the cost series has a gap.
3. A genuinely unavailable bucket remains `null` and breaks the affected series.

The backend owns this distinction when it constructs bounded buckets. The frontend does not infer business meaning from a missing array entry. It renders the supplied zero or null values, keeps isolated points visible, and keeps hover work limited to already computed rows. Empty ranges retain axes and baselines without inventing activity.

## Chat ranking and plan history

Chat ranking uses the supplied ChatGPT disclosure-list visual language and the current Mac data behavior. It has one rounded outer border, a quiet header row, full-width disclosure rows, and a lightly tinted expanded row. The first five chats are visible; “Show more” reveals five more up to the bounded page of 25. The expanded area shows model, reasoning effort, and speed distributions and the existing open-chat action. Column-resize handles and their persistence are removed from this surface.

Plan usage history uses the same disclosure-list system. The newest weekly period is open initially. A collapsed row shows its UTC date range and used allowance percentage. Expanded content shows the complete UTC start/end and data-as-of time, then the model breakdown. Approximate and partial-data truth remains visible without adding an extra nested card.

Both surfaces preserve current error, loading, empty, pagination, account-change, and stale-request behavior. New account responses cannot be overwritten by older requests.

## Weekly allowance estimates

`usage_week_intervals` remains the durable file-backed history. Valid records are no longer deleted simply because they are older than the most recent 100. Repricing still updates or removes records whose original evidence is no longer valid.

The foreground query returns at most the newest 20 rows for the current account. The UI has no pagination to older estimates. It uses a disclosure section containing a compact variable-height list: sampling interval on the left, allowance delta and weekly estimated value on the right. It shows exactly as many rows as exist, up to 20. With no rows it shows one compact empty line rather than a fixed-height table.

The database continues to retain plan, timestamps, start/end allowance, Token, consumed cost, weekly estimated cost, price version, and account identity. The visible row prioritizes the sampling interval, delta, and `estimated_total_usd`; detailed values remain available for later diagnostics without crowding the list.

## Pricing surface

The pricing table contains six columns: model, condition, input, cache read, cache write, and output. It has no per-row edit column.

The toolbar follows the Mac structure: search, immediate synchronization, edit selected model base price, and restore the selected model's automatic base price. Selecting any Standard, Fast, or long-context row selects its model. Editing always modifies that model's Standard base prices; Fast and long-context display rows continue to derive from the catalog rules. Restore replaces only that selected model's base override. The stable Codex model and condition ordering is retained. Displayed prices use a dollar sign and two decimals while calculations retain full precision.

## Floating window

Every floating style removes the Logo, product name, and decorative drag glyph. The entire non-preview content surface remains the native drag region, with browser text/image dragging disabled. There are no click, double-click, refresh, context-menu, or open-main actions in the content.

Style, quota scope, and dock direction each determine a natural size. Changing one of those inputs discards incompatible free-window dimensions and recomputes the new size. Free-window manual dimensions remain valid only while the same style and free layout remain active. One-allowance and two-allowance presentations have separate minimum sizes.

Docked top/bottom windows use a horizontal rounded capsule; left/right windows use a vertical rounded card. Both use a dark graphite surface with theme-independent readable text, a subtle border, and the configured transparency. Docking no longer forces black, square corners, or zero border. Edge changes preserve the content's visual center before constraining it to the target work area.

## Upstream detection state machine

Changing the upstream toggle is a two-stage action:

1. The UI asks the user to confirm enabling or disabling upstream detection. Cancelling performs no write.
2. The backend inspects live ChatGPT/Codex desktop processes, resolves their actual config root/profile, checks environment-variable overrides, resolves the effective provider route, and compares it with the owned Codexio route journal and active endpoint.

The backend returns structured state: desired enabled value, whether the route changed, whether a relevant desktop client is running, whether the active config already targets the current Codexio endpoint, whether restart is required, and a user-facing status/error. It writes only when the effective route needs to change. Concurrent user configuration changes continue to abort safely. An already-correct owned route is adopted without rewriting and without creating a false restart request.

When an affected client is running and an effective configuration change occurred, the UI presents the Mac choices: “Reopen later” and “Restart now”. Reopen later leaves a persistent status note. Restart now uses the existing normal-quit-and-relaunch implementation; it must not terminate unrelated shell `codex.exe` processes or force-kill an active task. If no affected desktop client is running, no restart modal appears.

Enable, disable, recovery on startup, and application quit share this state model so the toggle, route journal, relay process, and persisted preference cannot disagree.

## Settings synchronization layout

The read-only cloud synchronization controls form a dedicated action group. The invite field and Connect action occupy one responsive row. Upload read-only snapshot and Remove cloud occupy a second action row. At narrow widths each row wraps or stacks as a unit; controls never overlap, collapse below readable width, or merge with the connected-device actions.

## Error handling and stale work

- Failed font loading falls back to Windows interface fonts without blocking startup.
- Account and ranking requests preserve valid same-account data while reporting refresh errors.
- Chart null/zero meaning is fixed in the backend contract; the frontend never silently converts null to zero.
- Estimate persistence is append-only except for evidence-invalid repricing and explicit database lifecycle operations.
- Upstream writes retain the existing lock, digest, atomic write, and concurrent-change protection.
- Floating geometry is clamped to the current display work area after every size or dock transition.

## Verification

Implementation verification consists of:

- `svelte-check` with zero errors and zero warnings.
- Production Vite and Go/Wails compilation.
- The existing isolated smoke checks: program startup, basic data display, and main-window close/reopen.
- A bounded chart-data diagnostic proving empty buckets are zero and incomplete price buckets leave only cost null.
- A bounded rolling-history diagnostic proving more than 100 stored rows remain durable while only 20 are projected to the UI.
- Static inspection of upstream transition outputs for already-correct, changed-with-client, changed-without-client, and environment-override cases without modifying the user's actual configuration.
- Artifact version, source fingerprint, SHA-256, and manifest checks.

No screenshot or Computer Use result is required or claimed.
