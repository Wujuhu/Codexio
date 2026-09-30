# Windows migration from Mac v0.3.4

Reference: remote main 2c944cc, including fbb33f0 Mac/iOS accounting and sync fixes. Windows remains 0.3.4. Existing Noto Sans SC / Noto Serif SC fonts remain approved. No changes to Mac/iOS implementation, no remote publication.

- [x] Fetch and merge the user-published Mac/iOS source into the existing isolated Windows worktree.
- [x] Compare and migrate request classification, message identity, continuations, and on-demand source recovery from UsageIndexer, Database and Presentation.
- [x] Match overall logical sizing, compact quota/metric cards, log defaults/field picker, sidebar resizing/collapse and navigation drag behavior to Mac.
- [x] Reuse Mac prior-period metric calculations; render concise green/up and red/down comparisons without inflating metric values.
- [x] Match the actual Mac ChatGPT WHAM report requests, projections, account boundaries, chat ranking and plan history UI.
- [x] Compare all current iOS/Mac protocol fields, canonical digest, datasets and details; repair invalid synchronization at the failing contract.
- [x] Repair report metric alignment and preserve active Mac artwork; use the supplied month-report screenshot as evidence.
- [x] Restore Python floating-window appearance/behavior and expose a working switch in the main sidebar footer.
- [x] Review the scoped changes, compile/package once, run only the existing three isolated smoke checks, inspect one useful UI image if needed, verify delivery hashes/source/version, and commit locally.

Ownership: UI migration worker owns App/style/Overview/Metrics/Quota/Logs/Filters/Floating/Settings and their private helpers. Ledger worker owns collector/request/query/message/store code, excluding service.go and account/mobile code. Sync worker owns mobile*.go. Coordinator owns service/account APIs, Insights/Subscription/Report, native callbacks/build and integration. Workers first document the exact reference mechanisms and root causes; no new tests, SDKs, apps or remote actions.

Final combined build and original three mock checks passed. Physical Escape stopped Computer Use before a screenshot; GUI inspection was abandoned, and only the verified agent-owned mock process was closed. No installed real app was started, stopped, or inspected.
