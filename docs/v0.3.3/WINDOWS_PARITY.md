# Windows 0.3.3 parity and four-asset delivery

The user explicitly extended v0.3.3 to Windows on 2026-09-30. This supersedes the earlier Windows freeze for this version. The current Windows implementation is Python/PySide6 0.3.1; Mac is SwiftUI/AppKit 0.3.3. Windows keeps its existing tray/floating controls, while Mac WidgetKit and menu-bar configuration have no Windows equivalent.

## Behavioral parity

- [x] Update Windows source version to 0.3.3; align overview structure, icon selection, pricing order, reports and settings with Mac. Keep English/Chinese system-language behavior. Exact rendered parity still requires a real Windows build review.
- [x] Match Mac v0.3.3 data semantics for real user requests versus context compaction, recent completed task, model display order, token/fee/cache accounting, and bounded lists. The request-detail sync reads only a user message and final assistant reply.
- [x] Add the three native-style AI reports using the supplied assets. Daily/yesterday, weekly/previous complete Monday–Sunday week and monthly/previous complete month remain available every day. On or after 08:00 local time, the first eligible main-window opening presents once per local day; the month starts with monthly, Monday with weekly, otherwise daily. Default artwork is design 3. Share a full PNG; expose the storage path in Settings → Data. Load only active downsampled artwork and release it on close.
- [x] Bring Windows automatic updates to the Mac behavior: check first, then a main-window prompt with exact version, optional Release body, Update/Later, verified download progress, direct EXE link for every failure, normal shutdown/replacement/restart/rollback, and cleanup of unused installer packages. Actual installer replacement remains Windows-only validation.
- [x] Add read-only mobile monitoring/sync on Windows with the existing protocol and capacity bounds, including pairing, revocation, recent-task details and final replies. No remote execution or app-server control. Pairing with a real iPhone remains unverified.

## Delivery gates

- [x] Adapt the existing Windows CI, combined manifest, coordinator and verification to exactly four assets: `Codexio.exe`, `Codexio.app.zip`, `Codexio.ipa`, `latest.json`. Preserve the actual independent iOS version; validate its source fingerprint, device architecture, ZIP, size and SHA-256. Never publish a three-asset Windows result.
- [ ] Keep all build and diagnostic output in `build`. Mac and Windows-source isolated smoke passed; Mac signature/ZIP/Widget version and fresh iOS IPA verified. Windows x64 EXE metadata/MZ/hash requires CI after authorization.
- [ ] Commit to local Git after verified development artifacts. The repository rule requires a second explicit confirmation of release/version before the coordinator pushes, triggers Windows CI, publishes and archives to `release/0.3.3/`. No remote writes before that gate.

## Reused implementation

Current Windows `usage_collector.py`, `usage_queries.py`, `usage_worker.py`, `pricing.py`, `dashboard.py`, and `update_installer.py` provide the bounded collection/query/UI/update base. Mac source of truth is `macos/native/{MainViews,UsageReports,ReportArtwork,NativeUpdater,MobileSync,Database}.swift` and the existing report artwork ZIP in `build/dev/report-cards-234-source.zip`. The coordinator and Windows workflow now accept four assets; their first full CI run is pending the release confirmation gate.
