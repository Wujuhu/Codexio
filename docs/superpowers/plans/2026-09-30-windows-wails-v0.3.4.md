# Windows v0.3.4 Wails / Go Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development with disjoint ownership and the established interfaces below. User-requested completion is authorized; do not introduce another approval handoff. The latest AGENTS.md overrides test-suite and delivery defaults in skills.

**Goal:** Deliver the complete Windows v0.3.4 Wails/Go EXE, preserving the latest functionality and fixing automatic approval review classification/aggregation/counting.

**Architecture:** A single Wails process owns Go background services and a bounded TypeScript/Svelte UI. The durable SQLite ledger remains authoritative; derived request/call/query projections are updated only for relevant changes. Existing settings, history, price semantics, update and phone protocols are retained.

**Tech Stack:** Go 1.26.1 already available; Wails v3.0.0-beta.26; modernc.org/sqlite v1.60.1; TypeScript/Svelte/Vite from the official Wails template; Go standard libraries and existing compatible resources.

**Spec:** `docs/superpowers/specs/2026-09-30-windows-wails-v0.3.4.md` and the preserved functionality matrix in `2026-09-30-windows-v0.3.4-design.md`.

## Global Constraints

- Windows version 0.3.4; no changes to Mac/iOS application source/version/build numbers.
- Build/dev/staging/cache/checks/logs stay in `build`; deliver `build/dev/windows/Codexio.exe` + unified `latest.json` preserving other platform fields.
- No push, tag, release, remote deployment or Windows CI.
- No Python/PySide in the new EXE; no full Visual Studio/.NET/C++ SDK installation.
- Only the fixed three mock smoke items; no new persistent test files/suites or pytest. No real app/client restart or production configuration change in verification.
- Read text explicitly as UTF-8; bound caches, DB reads, protocol payloads and background work; unknown data remains unknown.
- Root owns module manifests, common contract/helpers, application integration and build scripts. Worker ownership is disjoint, and workers do not create child agents or commit another worker's files.

## Review Focus

- Forked/replayed responses retain immutable owner/counters and are deduplicated.
- Guardian with only parent chat stays independent and excluded from user count; explicit unique parent turn merges without changing global accounting.
- Partial token/cache/price data retains missingness instead of producing zero or a false complete amount.
- Old queries/account results are discarded; inactive surfaces do not poll or reaggregate history.
- Shutdown/update/proxy/configuration changes remain confined to app-owned resources and retain rollback/foreign settings.

### Task 1: Root foundation and interfaces

**Files:** `windows/go.mod`, `windows/backend/types.go`, `windows/backend/values.go`, `windows/backend/CONTRACT.md`, Wails bootstrap and frontend scaffold.

- [x] Pin Go/Wails/SQLite dependencies and use project-local CLI/cache directories; verify dependency downloads and official template/build conventions.
- [x] Define the `Row`, `Query`, `PageResult` and public service contract below before parallel implementation. Frontend imports only its API wrapper; all IO remains behind Go calls.
- [x] Preserve brand/report resources, resize packaging copies without changing original artwork, and establish the module/version/asset paths.

### Task 2: Ledger, collector, price and request projections

**Owner:** data implementer. **Files:** `windows/backend/store.go`, `collector.go`, `requests.go`, `pricing.go`, `queries.go` and data-specific helper files; embedded seed in `backend/assets`.

**Consumes:** common contract/helpers and `DataOptions`.
**Produces:** `OpenStore(DataOptions) (*Store,error)`; `(*Store).Run(context.Context, func())`; `Configure(Row)`; `Status() Row`; `Generation() int64`; `Database() *sql.DB`; `Page(Query) (PageResult,error)`; `Summary(Query) (Row,error)`; `Chart(Query) ([]Row,error)`; `Models(Query) ([]Row,error)`; `Insights(Query) (Row,error)`; `Recent(Query,int) ([]Row,error)`; `Detail(string,int) (Row,error)`; `Filters() (Row,error)`; `Prices() []Row`; `SetPrice(string,Row) error`; `Refresh()`; `Rescan()`; `Close() error`.

- [x] Port proven schemas, origins/cursors, append scanning, source title discovery, change journals and durable IDs from current Python and v0.2.10 references.
- [x] Port verified token/price/cache rules and configuration overrides; isolate source timestamps from price content signature. Model sync preserves prior prices on offline/conflicting data.
- [x] Fix Guardian source retention and request kinds; preserve aliases, continuations, compaction and internal agent edges; never infer independent-request merges from time or identical text.
- [x] Build atomically updated priced/group/member indices and bounded SQL pages/summary/chart/model/insight APIs. Request count is true root user groups only throughout.
- [x] Preserve bounded request message detail and attachments, actual user/final extraction, legacy fallback without pretending truncated text is full; map local chat titles/name preferences.
- [x] Provide isolated mock initialization for the existing three-item application smoke, without creating a new fixture/test suite.
- [x] Return a concise report of source references, interfaces, compilation status and unresolved issues; root reviews before final integration.

### Task 3: Quota, preferences, official reports and verified updater

**Owner:** system implementer. **Files:** `windows/backend/config.go`, `quota.go`, `rpc.go`, `analytics.go`, `updater.go`, app-owned process helpers with platform suffixes.

**Consumes:** common contract/helpers and `SystemOptions`; root supplies thread summaries to official reports.
**Produces:** `LoadConfig(string) (Row,error)`; `SaveConfig(string,Row) error`; `NewQuota(SystemOptions) *QuotaService`; `Start(context.Context)`; `Snapshot() Row`; `Refresh()`; `ResetCredit(string) (Row,error)`; `ReadReports([]Row,bool) (Row,error)`; `Close() error`; `NewUpdater(SystemOptions) *Updater`; `Check(context.Context) (Row,error)`; `Install(context.Context) error`; `Snapshot() Row`; `RunUpdateHelper([]string) (bool,error)`.

- [x] Preserve settings/analytics files, defaults, old keys and data directory discovery; expose only public preferences, never credentials to frontend/logs.
- [x] Discover Codex CLI, serialize JSON-RPC, process notifications, account identity/API-key mode, freshness, retries and cancellation; own and clean up only the app-server child tree.
- [x] Port selected reset-credit idempotency and official read-only account history/thread report contracts with bounded requests/account invalidation and missing-data states.
- [x] Port update manifest/version selection, restricted URLs, download progress/integrity, explicit user install, helper wait/replace/ack/rollback and cache cleanup. Mock never installs or reads real auth/config.
- [x] Report verified interfaces and platform/network limitations. Do not run a real app-server/update against the user's live application.

### Task 4: Existing visual system and complete bounded UI

**Owner:** UI implementer. **Files:** `windows/frontend` except root-provided brand copies; own package source/config/lock as agreed, no Go files.

**Consumes:** frontend API contract in `windows/backend/CONTRACT.md`; generated Wails bridge or stable `Call.ByName` wrapper. Root prepares assets and services.

- [x] Implement six pages, preserved navigation/settings/theme/language/icons, overview/quota/stat cards and recent true tasks, logs/filter/paging/column widths, interactive prepared charts/heatmap/model shares and subscriptions.
- [x] Distinct automatic approval review/compaction labels and counts; inspectable actual user/final reply, attachments and member/model composition without replacing the main task content.
- [x] Three report styles, closed-period data, once/day-after08:00 presentation, current-only artwork and full PNG export; settings/data path and share action.
- [x] Native-window-routed floating view, quota modes, UI controls for dock/top/bottom/tray, updater/reset confirmations/progress/error/direct-link states, upstream and mobile pairing/settings controls.
- [x] Use asynchronous bounded requests, reject stale responses, event-triggered refresh for active surface, keyboard/focus/empty/error/loading behavior and actual source-based brand. Do not add general page/screenshot traversal tests.
- [x] Compiler/type check once complete; coordinate one current UI inspection and only necessary corrective pass with root.

### Task 5: Root service composition, reports, upstream and mobile

**Files:** `windows/backend/service.go`, `reports.go`, `upstream.go`, `mobile.go` plus narrowly scoped helpers; `windows/main.go`, desktop/platform/smoke integration.

- [x] Bind the agreed public service methods, derive reports/mobile from the same classified ledger, start/cancel services coherently and publish only relevant changed state.
- [x] Port opt-in upstream route management, guarded HTTP/SSE/WebSocket observation and restoration/legacy ownership; mock never touches real Codex route/auth.
- [x] Port phone DPAPI credentials/TLS/Bonjour/WebSocket/canonical envelopes, pairing approval/revocation, bounded recent/trend/message details and existing cloud protocol. No remote execution or deployment.
- [x] Native lazy main window, floating window, tray, existing-instance handling, geometry/preferences and proper close-versus-quit ownership.
- [x] Adapt fixed mock smoke entry to three checks only; keep production and mock data/config/process ownership separated.

### Task 6: Review, packaging, delivery and local commit

**Files:** `build_exe.ps1`, `run.ps1`, Windows-specific resource config/version, shared manifest/fingerprint integration where required, Windows documentation.

- [x] Resolve interface conflicts and run Go/frontend compilation. Conduct fresh-context task/whole-change reviews appropriate to scope, fix material findings.
- [x] Build to `build/staging/windows`, perform exactly startup/basic data/close-reopen isolated smoke and directly related Guardian verification; inspect one current interface image if needed.
- [x] Verify EXE metadata 0.3.4, MZ, size/SHA-256 and unified manifest; use existing safe publisher, preserving staging if occupied. Keep older unrelated apps/releases untouched.
- [x] Commit verified product changes locally; integrate the worktree to the user's local checkout when safe and deliver the development package there. No remote write.
- [x] Report actual check results, measured process memory only with conditions and children included, and unverified real account/phone/update/Linux boundaries.
