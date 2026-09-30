# Exact Mac layout and observed-data repairs

User supersedes earlier UI density/comparison tweaks: match current Mac v0.3.4 directly, with the already approved Noto fonts. Floating window remains the existing Python reference because Mac has no floating host. Windows version stays 0.3.4. No Computer Use or screenshots for verification.

Evidence: running primary build/dev/windows EXE SHA-256 88c22ae7 matches the last delivery, so this is not an old-package mismatch. Read-only SQLite audit shows four wrapper-only external_codex_apps_open_page rows marked has_user_message and counted as user_request. Header update time is not projected into log-page data; initial settings snapshot may precede the first scan.

- [x] Migrate actual-input filtering and repair retained wrapper metadata without replaying meters or touching live data during development.
- [x] Diagnose the reported WHAM failures against current Mac contracts, using a minimal read-only request when necessary; fix credential/projection errors, not empty-state copy alone.
- [x] Match Mac shell/log/settings/chart dimensions; remove artificial preview clipping; scroll only settings content; omit empty update status/progress/release information; retain empty-chart baseline.
- [x] Correct floating transparency/native host behavior against the Python source; preserve native docking/visibility and existing brand assets.
- [x] Publish scan time through a local header-only clock event, independent of business data snapshots and unchanged scan generations.
- [x] Review the scoped changes, compile/package once with the existing three mock smoke checks, verify version/hash/source/manifest, and commit locally. Preserve the user's running executable and deliver new code to staging when occupied.

Ownership: ledger worker owns collector/request/message/query/store files and DataOptions scan-time callback; UI worker owns frontend shell/log/settings/chart and global styles; account worker owns analytics/chat_ranking/subscription/quota account contracts. Coordinator owns service integration, native floating host and Floating component, docs/build/final integration. No tests or test-suite expansion, no installed app start/restart, no Computer Use, no Mac/iOS edits or remote publication.

Final production build succeeded after declaring the existing x/net import and its pinned x/text dependency. Svelte: zero errors/warnings. Original three isolated mock smoke checks passed. No Computer Use, app inspection or screenshots. Scoped source review resolved the incomplete-preview ownership issue; actual read-only WHAM and native static-proxy GET evidence is retained under build/checks.
