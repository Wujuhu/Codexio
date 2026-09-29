# Report reader design QA

final result: passed

## Source and evidence

- Selected style: source package design 3, mint cat garden.
- Initial visual reference: build/checks/v0.3.3/report-preview.png, 1300 × 2480 pixels, logical 650 × 1240 at 2×.
- Current shared card export: build/checks/v0.3.3/report-reader-fix/report-long.png, same dimensions and sample data.
- Implementation: build/checks/v0.3.3/report-reader-fix/report-window-zh.png, 1320 × 1180 pixels, native 660 × 590 window at 2×.
- State: Chinese, daily report, 2026.09.28, garden style, isolated sample data.
- The long export and native first viewport were opened together for visual comparison. The reader is a viewport into the 560-point card; toolbar and scroll cue are outside the card. Align the card rather than comparing complete image outlines of different heights.

## Findings and correction history

1. P1, original implementation: the native reader replaced the selected cat card with a flat metrics panel. Cat ears, editorial type, illustration proportion, progress bar and original content hierarchy were missing. The user's supplied screenshot is the before evidence.
2. The first correction reused the card surface and sections in two pages. During that work the user explicitly changed the requirement to one continuous long page. That intermediate layout was superseded.
3. Final correction: removed ReportReaderPage and its independent layout. UsageReportLiveCard embeds the same UsageReportCard used by UsageReportRenderer. Paging and page indicators were removed. The final native screenshot and long export above are post-fix evidence.

## Required visual surfaces

- Typography: shared Songti SC editorial headings and numeric components; the reader no longer substitutes its own rounded system-font dashboard headings.
- Layout rhythm: shared card dimensions, cat ears, insets, section rules and content flow. Window clipping at the bottom of the first viewport is expected for the user-requested continuous long page, with a visible scroll cue.
- Colors: shared cream paper and mint palette; period controls use the card palette.
- Assets: original supplied brand and garden cat PNGs, without replacements or recomposed illustrations.
- Content: the same report data is passed to the on-screen and export card. Period priority remains month-first on day 1, then weekly on Monday, otherwise daily.
- Full-view and first-viewport text, illustration and card details are readable in the paired comparison; no additional focused screenshot was needed.

## Verification limits

Native Mac build, existing three-point isolated smoke, signature and archive checks passed. The capture used NSHostingView containing the production reader. No installed application was launched. No screenshot matrix, new automated test item, or production data scan was introduced. This visual pass covers the selected daily garden view; other period/style combinations were not separately captured.
