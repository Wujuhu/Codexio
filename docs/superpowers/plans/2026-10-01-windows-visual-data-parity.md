# Windows Visual and Data Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows 0.3.4 follow the approved Mac/ChatGPT display, data-retention, floating-window, and upstream-detection behavior without changing other platforms or expanding the test suite.

**Architecture:** Keep the existing Go/Wails boundaries: Go owns data meaning, persistence, process/config inspection, and native geometry; Svelte owns prepared-data rendering and responsive layout. Add shared presentation primitives once, then migrate each surface to them. Complete all source changes before one production package/smoke run, as required by the repository's minimal-test policy.

**Tech Stack:** Go 1.26.1, Wails 3 beta.26, Svelte 5, TypeScript, CSS, SQLite, PowerShell build pipeline.

**Spec:** `docs/superpowers/specs/2026-10-01-windows-visual-data-parity-design.md`

## Global Constraints

- Keep `windows/VERSION` at `0.3.4`; do not modify Mac or iOS sources.
- Do not create test files, run pytest, add UI traversal, generate screenshot matrices, or use Computer Use.
- Reuse current Mac source and `v0.2.10` Windows/Python behavior where the spec names them.
- Preserve null/unknown data; only a confirmed no-activity chart bucket becomes zero.
- Keep all filesystem, SQLite, network, and process inspection off the frontend.
- Do not start, stop, or restart the user's real Codexio or ChatGPT applications during verification.
- Build once after all source changes; run only the existing three isolated smoke checks.
- Commit locally; do not push or publish.

## Review Focus

- Chinese locale must still render `1.2K`, `179M`, and `1.27B` outside reports, while report Token labels retain their existing Chinese report notation.
- A no-call chart bucket must join the baseline; a priced-unknown bucket must keep Token and break only cost.
- Account-report responses from an earlier account/filter must never overwrite a later result after the UI redesign.
- Floating style/scope/dock changes must discard incompatible dimensions without moving the surface outside the current monitor work area.
- Enabling an already-correct owned upstream route must not rewrite config or request a restart; an environment override must fail without writing.

---

### Task 1: Typography, compact numbers, metric copy, and chart bucket contract

**Files:**
- Create: `windows/frontend/public/fonts/InterVariable.woff2`
- Modify: `windows/frontend/src/style.css`
- Modify: `windows/frontend/src/lib/api.ts`
- Modify: `windows/frontend/src/components/Metrics.svelte`
- Modify: `windows/frontend/src/components/Chart.svelte`
- Modify: `windows/backend/queries.go`
- Modify: `docs/v0.3.4/WINDOWS_WAILS.md`

**Interfaces:**
- Produces: `compact(value: any): string` with deterministic `K/M/B` output.
- Produces: `Store.Chart(Query) []Row` containing every bounded bucket with separate known-zero and unknown-cost values.
- Produces: `chartBucketKeys(start time.Time, end time.Time, granularity string) []string` for finite bounded ranges.
- Consumes: existing `metricSum.row()` completeness fields and report-specific `Report.svelte` number formatting.

- [ ] **Step 1: Add the supplied Inter asset**

Extract only `InterVariable.woff2` from `C:\Users\WJH\Downloads\Inter-4.1.zip` into `windows/frontend/public/fonts/`. Record the upstream archive/version and license location in `docs/v0.3.4/WINDOWS_WAILS.md`; do not install the font system-wide or copy unrelated font variants.

- [ ] **Step 2: Define the application font stack**

Add `@font-face` for `Codexio Inter` and set the app/control font stack to `"Codexio Inter", "Microsoft YaHei UI", system-ui, sans-serif`. Keep report artwork-specific sizing and layout intact.

- [ ] **Step 3: Replace locale compact notation**

Implement `compact(value)` in `api.ts` using thresholds `1_000`, `1_000_000`, and `1_000_000_000`, suffixes `K/M/B`, at most one fractional digit, and no trailing `.0`. Preserve `—` for invalid/missing values. Do not change `Report.svelte`'s `tokenLabel` path.

- [ ] **Step 4: Remove the cost-card pricing annotation**

Delete the `pricing-note` child from `Metrics.svelte`. Keep completeness metadata in the data object for tooltips/details and keep existing unpriced-call status surfaces elsewhere.

- [ ] **Step 5: Materialize bounded chart buckets in Go**

Add a focused helper in `queries.go` that enumerates the selected local-time hour/day/week/month range only when bounds are finite and within the existing 730-day limit. For absent buckets return Token, cost, requests, and component counts as known zero. For buckets with calls use `metricSum.row()` unchanged so incomplete cost remains null while Token remains known. Keep cache keys dependent on the existing query/granularity/time bounds.

- [ ] **Step 6: Make the chart render the backend contract directly**

Remove `Chart.svelte`'s synthetic `missing:true` insertion. Preserve null-series path breaks, isolated points, prepared hover rows, empty axes, and baseline grid.

- [ ] **Step 7: Run a bounded data diagnostic**

Use an isolated SQLite/build-staging diagnostic, not a new maintained test file, to assert: an empty middle bucket has `tokens=0` and `usd=0`; a bucket with unpriced calls has known Token and null cost; frontend date order is continuous. Preserve a concise result under `build/checks/v0.3.4/`.

- [ ] **Step 8: Commit Task 1**

Commit the font, formatter, metric, chart, backend contract, and documentation together with a message describing display foundations and chart continuity.

### Task 2: Chat ranking, plan history, weekly estimates, and pricing

**Files:**
- Modify: `windows/frontend/src/components/ChatRanking.svelte`
- Modify: `windows/frontend/src/components/Subscription.svelte`
- Modify: `windows/frontend/src/components/Prices.svelte`
- Modify: `windows/frontend/src/style.css`
- Modify: `windows/backend/rolling.go`
- Modify: `windows/backend/service.go`
- Modify: `windows/backend/pricing_sync.go`
- Modify: `windows/backend/CONTRACT.md`

**Interfaces:**
- Consumes: existing `GetChatRanking(metric,page)` and stale-response sequence guards.
- Produces: `RollingEstimator.Rows(account string)` returning newest 20 display rows while SQLite retains all valid evidence.
- Produces: private `syncPrices(ctx context.Context, force bool)`, store method `RefreshPrices(ctx context.Context) error`, and Wails method `Service.SyncPrices() (Row, error)` returning the refreshed `GetPrices()` projection.
- Consumes: existing `SavePrice(model,rates)` empty-rates restore semantics.

- [ ] **Step 1: Simplify chat ranking structure**

Remove column-width state, resize handlers, saved-width writes, and resize handles. Render the supplied ChatGPT/Mac disclosure list with stable grid columns, five initial rows, five-row increments, a 25-row cap, existing pagination, and existing sequence invalidation. Retain official/local mode, sort actions, data-as-of time, partial-data truth, distributions, and open-chat action.

- [ ] **Step 2: Restyle plan history with the same disclosure primitive**

Keep only one period expanded, with the newest open initially. The summary row shows UTC period and percent; expanded content shows full UTC boundaries, data-as-of, models, approximate/partial status, and no nested card.

- [ ] **Step 3: Preserve estimate history and project only 20 rows**

Remove the `DELETE ... LIMIT 100` retention operation from `NewRollingEstimator` and insertion. Reprice every stored interval whose price version differs rather than selecting only 100. Change `Rows(account)` to query newest 20 for the current account.

- [ ] **Step 4: Replace the estimate table with a compact list**

Delete pagination and fixed table layout from `Subscription.svelte`. Render at most 20 rows with sampling interval, delta percent, and `estimated_total_usd`; use one compact empty row when absent. Keep the explanatory evidence-boundary sentence.

- [ ] **Step 5: Expose explicit price synchronization**

Refactor `pricing_sync.go` so the scheduled `syncPrices(ctx,false)` path keeps its retry gate while `RefreshPrices(ctx)` acquires the store work lock and calls `syncPrices(ctx,true)`. Forced sync bypasses only the checked-at delay, retains two-source validation/conflict behavior, honors cancellation, and reloads the catalog. Expose `Service.SyncPrices() (Row,error)` and document the Wails contract.

- [ ] **Step 6: Rebuild the pricing toolbar and selection model**

Add search and row selection. Remove the edit column and per-row buttons. Add immediate sync, edit selected base price, and restore selected automatic price actions. A selected Fast/long-context row resolves to its model; the editor loads only that model's Standard threshold-zero row. Display all prices with `$` and exactly two decimals.

- [ ] **Step 7: Run bounded persistence and projection diagnostics**

In an isolated build-staging database create more than 100 valid estimate rows, reopen the estimator, and verify all remain stored while `Rows(account)` returns exactly the newest 20. Exercise forced price sync only with a local/cancelled diagnostic or static path inspection; do not make correctness depend on external network availability.

- [ ] **Step 8: Commit Task 2**

Commit ranking, subscription, estimator, price service, and pricing UI changes as one coherent data-surface update.

### Task 3: Floating-window content and geometry

**Files:**
- Modify: `windows/frontend/src/components/Floating.svelte`
- Modify: `windows/frontend/src/style.css`
- Modify: `windows/backend/service.go`
- Modify: `windows/desktop.go`
- Modify: `windows/backend/config.go` only if preference normalization requires a validated marker

**Interfaces:**
- Produces: normalized floating settings where incompatible free dimensions are null after style/scope/dock changes.
- Consumes: `desktopFloatingSize(Row)`, `desktopFloatingLimits(Row)`, and the existing center-preserving `endFloatingInteraction` logic.

- [ ] **Step 1: Remove brand content from every floating variant**

Delete free-window and docked Logo/name/grip markup. Keep only quota labels, percentages, meters/rings, reset time where the style includes it, and optional numeric summary. Preserve display-only behavior and browser drag prevention.

- [ ] **Step 2: Normalize dimensions on layout-setting changes**

Before persisting `visual_style`, `quota_scope`, or `dock_edge`, clear free dimensions that belong to the old layout and clear the target dock dimension pair when its orientation changes. Do not clear position for style-only changes. Apply settings once after the normalized merge.

- [ ] **Step 3: Define one- and two-window natural sizes**

Update `desktopFloatingSize` and limits so each visual style has explicit one-allowance and two-allowance dimensions. Free custom size applies only to the current compatible free layout; dock dimensions remain orientation-specific and bounded.

- [ ] **Step 4: Restyle docked surfaces**

Use rounded horizontal capsules and vertical cards with graphite translucent background, subtle border, configured opacity, and readable status colors. Do not force border radius or border width to zero in docked CSS. Keep system work-area clamping and center anchoring.

- [ ] **Step 5: Perform static geometry diagnostics**

Use a short build-staging Go diagnostic to evaluate all six styles × one/two allowance × five dock states. Assert positive bounded dimensions and centered edge transitions without starting a real window. Do not add a maintained test file.

- [ ] **Step 6: Commit Task 3**

Commit floating markup, CSS, preference normalization, and geometry as one change.

### Task 4: Upstream transition state and responsive cloud controls

**Files:**
- Modify: `windows/backend/upstream.go`
- Modify: `windows/backend/upstream_windows.go`
- Modify: `windows/backend/service.go`
- Modify: `windows/backend/CONTRACT.md`
- Modify: `windows/frontend/src/components/Settings.svelte`
- Modify: `windows/frontend/src/components/Mobile.svelte`
- Modify: `windows/frontend/src/style.css`

**Interfaces:**
- Produces: `UpstreamAction(enabled bool) Row` with `enabled`, `desired_enabled`, `status`, `error`, `route_changed`, `client_running`, `already_targeted`, and `restart_required`.
- Consumes: existing process discovery, effective profile resolution, journal digest, atomic config write, normal desktop restart, and settings event paths.

- [ ] **Step 1: Separate upstream inspection from mutation**

Add an internal transition/inspection result that records live desktop-client presence and effective route ownership before mutation. Reuse `upstreamDesktopClients`, `upstreamResolve`, journal validation, and environment-override checks. Do not broaden process matching beyond verified ChatGPT/Codex desktop roots.

- [ ] **Step 2: Make Toggle report actual transition facts**

Set `route_changed` only after a successful effective config change. Set `restart_required` only when `route_changed && client_running`. If the current owned route already targets the active Codexio endpoint, adopt/retain it without rewriting and return `already_targeted=true`. Preserve rollback and concurrent-write rejection.

- [ ] **Step 3: Keep persisted preference and runtime state atomic**

Update `Service.UpstreamAction` so preference persistence follows a successful transition and failure returns the prior public state. Startup recovery, disable, quit preparation, and `GetSettings` use the same fields. Update `CONTRACT.md`.

- [ ] **Step 4: Add Mac-equivalent confirmation and restart choices**

In `Settings.svelte`, intercept the toggle and show an enable/disable confirmation before calling `UpstreamAction`. After an effective route change with a running client, show “稍后自行重启” and “现在重启”. Later keeps a visible status; restart invokes the existing normal restart and refreshes settings afterward. No modal appears when no client is running or no route changed.

- [ ] **Step 5: Repair cloud synchronization layout**

Give `Mobile.svelte` dedicated `cloud-enroll-row` and `cloud-action-row` containers. Keep invite+connect together and upload+remove together; add responsive CSS that stacks each row below its minimum width without overlapping connected-device controls.

- [ ] **Step 6: Run non-mutating transition diagnostics**

Exercise internal/static transition construction for already-targeted, changed-with-client, changed-without-client, and environment-override inputs. Do not write the user's config or restart a client. Record the bounded result under `build/checks/v0.3.4/`.

- [ ] **Step 7: Commit Task 4**

Commit upstream state, settings confirmation/restart UI, cloud layout, and contract changes together.

### Task 5: Integrated review, one build, delivery, and local commit state

**Files:**
- Modify: `docs/v0.3.4/WINDOWS_WAILS.md`
- Generated under ignored paths: `build/staging`, `build/cache`, `build/checks`, `build/logs`, `build/dev/windows` or `build/dev/windows/pending`

**Interfaces:**
- Consumes: all preceding task interfaces.
- Produces: verified Windows 0.3.4 development EXE, preserved occupied target behavior, manifest, delivery record, and clean local Git state.

- [ ] **Step 1: Review the complete scoped diff**

Run `git diff --check`, verify `windows/VERSION` remains `0.3.4`, confirm reports still use report-specific Token formatting, confirm no Mac/iOS/source-test files changed, and check all eight user requirements against the spec.

- [ ] **Step 2: Run Impeccable's mechanical detector once**

Run the detector on the changed Svelte/CSS targets. Resolve concrete overflow, control, font, and responsive findings in one bounded edit pass; do not use screenshots or Computer Use.

- [ ] **Step 3: Build and run the existing smoke once**

Run `build_exe.ps1`. Required evidence: `svelte-check` reports zero errors/warnings, Vite production build succeeds, Go/Wails production build succeeds, and the smoke result contains exactly program startup, basic data display, and main-window close/reopen.

- [ ] **Step 4: Verify delivery integrity**

Verify EXE version `0.3.4`, MZ header, source fingerprint, manifest platform fields, size, SHA-256, and the delivery record. If the development EXE is running, preserve it at its original path and deliver the new artifact to `build/dev/windows/pending`.

- [ ] **Step 5: Finalize documentation and commit state**

Record implemented references, contracts, diagnostics, limitations, and the no-Computer-Use verification in `WINDOWS_WAILS.md`. Commit any final integration corrections locally. Confirm `git status --short` is empty and do not push or publish.
