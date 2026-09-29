# Report reader design QA

final result: passed

## Source and evidence

- Selected style: source package design 3, mint cat garden.
- Initial visual reference: build/checks/v0.3.3/report-preview.png, 1300 × 2480 pixels, logical 650 × 1240 at 2×.
- Current shared card export: build/checks/v0.3.3/compact-report-final/report-compact.png, 1180 × 2120 pixels, logical 590 × 1060 at 2×.
- Implementation: build/checks/v0.3.3/compact-report-final/report-window-zh.png, 1320 × 1180 pixels, native 660 × 590 window at 2×.
- State: Chinese, daily report, 2026.09.28, garden style, isolated sample data.
- The compact export and native default window were opened together for visual comparison. The 500 × 980 point card follows the supplied narrow, tall reference and is scaled from both available width and height to remain fully visible in the default window.

## Findings and correction history

1. P1, original implementation: the native reader replaced the selected cat card with a flat metrics panel. Cat ears, editorial type, illustration proportion, progress bar and original content hierarchy were missing. The user's supplied screenshot is the before evidence.
2. The first correction reused the card surface and sections in two pages. During that work the user explicitly changed the requirement to one continuous long page. That intermediate layout was superseded.
3. Final correction: removed ReportReaderPage and its independent layout. UsageReportLiveCard embeds the same UsageReportCard used by UsageReportRenderer. Paging and page indicators were removed. The final native screenshot and long export above are post-fix evidence.
4. User requested a smaller, narrower and taller card that normally fits one page, then supplied the original long-card composition as the target. The card is now 500 × 980, project rows are replaced by usage rhythm, first/last clock times and three model rows, and the default window includes the full scaled card and share button.

## Required visual surfaces

- Typography: shared Songti SC editorial headings and numeric components; the reader no longer substitutes its own rounded system-font dashboard headings.
- Layout rhythm: shared 500 × 980 card dimensions, centered date, large editorial headline, cat ears, generous vertical spacing, section rules and content flow. The complete scaled card and share button fit the default 660 × 590 window; smaller windows keep a scroll fallback.
- Colors: shared cream paper and mint palette; period controls use the card palette.
- Assets: original supplied brand and garden cat PNGs, without replacements or recomposed illustrations.
- Content: the same report data is passed to the on-screen and export card. Project rows are replaced by four time periods, earliest/latest clock times and model cost/Token rows. Period priority remains month-first on day 1, then weekly on Monday, otherwise daily.
- Full-view and first-viewport text, illustration and card details are readable in the paired comparison; no additional focused screenshot was needed.

## Verification limits

Native Mac build, existing three-point isolated smoke, signature and archive checks passed. The capture used NSHostingView containing the production reader. No installed application was launched. No screenshot matrix, new automated test item, or production data scan was introduced. This visual pass covers the selected daily garden view; other period/style combinations were not separately captured.
