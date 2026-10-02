# Windows 0.3.6 Mac/iOS parity implementation plan

**Goal:** Align the existing Windows desktop and mobile-host projections with the user-specified functionality integrated by `47666beedd65904af8d2aea96facf4083f5358fe`.

**Architecture:** Keep Wails + Go + Svelte, the existing report flow, native window lifecycle, bounded ledger queries and mobile dataset revision/digest pipeline. Add only the missing presentation and projection logic. Independent changes use disjoint files and are integrated before one final development build.

**Spec:** The user's 2026-10-02 Windows parity request and the referenced production Mac/iOS sources at `47666be`.

## Global constraints

- `windows/VERSION` remains `0.3.6`; Mac/iOS/Python versions and release/cloud deployment code are out of scope.
- No push, Release, remote CI, cloud deployment, or real-client launch/restart/termination.
- No added test suite, test data, UI traversal or screenshot matrix. Use the existing three-item isolated smoke through `build_exe.ps1`, plus delivery checks and narrowly scoped inspection.
- Preserve fractional Unix seconds, true active execution intervals, unknown values, user navigation order, upstream model observations, adjustable columns and pagination.
- Build outputs stay under `build`; occupied application paths stay intact.

## Tasks and references

- [x] Fetch and fast-forward main; confirm `47666be` and the existing Windows fixes are present.
- [x] Report-cat control: adapt `macos/native/ReportCatAnimation.swift` placements, anchors and timing into one small Svelte control using committed `macos/Resources/ReportCat` assets. Use display-sized DPI variants, fixed 32×32 hit area, independent composited tail/greeting animations, visibility/focus/reduced-motion gating. Wire into the existing `App.svelte` report action before the update stamp.
- [x] Active duration: compare Windows request grouping in `requests.go` against `UsageRow.duration` in `macos/native/Presentation.swift`; deduplicate/merge execution intervals, preserve true start and final time, expose stable base/current-start values to desktop and mobile summaries. Keep fractional seconds and reuse `Duration.svelte`.
- [x] Formatting: centralize model/effort/speed, brief/full local dates and localized duration in `logFormat.ts`; apply across overview, logs, details, usage and report without replacing their current data/UI flows.
- [x] Mobile all-history: adapt `MobileSync.swift` `trendDays` and all-history export to the Go background projection. Keep 7/30/0 periods, 30 recent calendar days and at most 60 older buckets, covering every record exactly once. Reuse generation/time-zone/day invalidation and protocol serialization.
- [x] Integration: preserve existing desktop all-range query and saved navigation ordering; inspect LAN/cloud/cache propagation of `durationStarted`, `durationBase` and `days` against `apple/shared/MobileProtocol.swift` and deployed Worker schema.
- [x] Build/verify: run existing Windows build and three-item isolated smoke; verify EXE version, source fingerprint, manifest/hash and protected platform files. Review only a directly relevant UI preview if needed.
- [x] Record final implementation/verification and commit locally.

## Review focus

- Duplicate or overlapping execution intervals, resumed tasks with waiting gaps, unknown starts and terminal durations.
- Unfocused/hidden/minimized windows and reduced motion stop animations; greeting never restarts the tail.
- PNG source canvas, CSS coordinate signs and rotation anchors remain faithful at normal/high DPI and both themes.
- All-history totals include data older than 90 days; calendar buckets respect host timezone/DST and remain bounded.
- Mobile anchors are stable numbers, survive both transports/cache, and do not cause per-second projection uploads.

## Completion notes

Implemented against `47666be` without changing Windows 0.3.6 or any protected platform/version/deployment files.

- `ReportCatButton.svelte` uses the exact Mac placements/anchors, independent WAAPI animations and visibility/focus/reduced-motion gating. `render_windows_report_cat.py` derives 20 bounded DPI PNGs (23,527 bytes total) directly from the five committed originals. The original report action and navigation sorting are reused.
- `request_duration.go` merges actual execution intervals and publishes one stable active anchor/base. `requests.go` invalidates derived duration projection version 2 once; collector metadata version 4 reuses bounded source recovery for authoritative `payload.started_at`. Pricing caches and immutable metering cursors stay valid. Unknown aggregate requests now clear prior segment end timestamps.
- `logFormat.ts` centralizes request labels, dates and localized durations. Existing `Duration.svelte` shares one clock only among visible foreground active labels. `request_format.go` normalizes mobile/request metadata without modifying pricing fallbacks. Existing log columns, model observations and bounded pagination are retained.
- `mobile_projection.go` exports complete periods 7/30/0 through the existing live/recent/trends pipeline, with calendar-based bounded history buckets. `durationStarted`/`durationBase` remain fractional numeric seconds; source, LAN/cloud serialization, retention and disk-cache paths preserve the fields. Deployed Worker schema was inspected read-only.

Final verification: `build_exe.ps1 -Version 0.3.6` passed type checking (0 errors, 0 warnings), production compilation and the original three isolated checks (startup, basic data, close/reopen). Verified source fingerprint, manifest and EXE SHA-256. One browser preview reused existing isolated-smoke data and confirmed the 32×32 hit area, five DPI-selected layers, formatted dates/model names and report opening from the button corner. No maintained tests or fixtures were added.

Delivery: `build/dev/windows/pending/Codexio.exe` and `latest.json`, because the current `build/dev/windows/Codexio.exe` is running and was preserved. Delivery metadata is in `build/checks/v0.3.6/delivery.json`; the single preview is `windows-parity-preview.png` in that directory.

Not performed: physical iOS LAN/cloud end-to-end sync, real Windows minimized/focus/reduced-motion/DPI interaction checks, or runtime DST data validation. Those lifecycle/calendar paths were inspected in source. Legacy timestamp recovery remains bounded across background passes; unavailable source evidence displays unknown. No push, Release, remote CI or cloud deployment.
