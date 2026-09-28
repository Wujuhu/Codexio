# Codexio iOS showcase

A 32-second, 1920 x 1080, 30 fps Remotion composition. Silent H.264 MP4.

## Deliverables

- Video: `../../dev/video/Codexio-iOS-Showcase.mp4`
- Poster: `../../checks/codexio-ios-video/poster.png`
- Composition: `Codexio-iOS`

## Reuse and fidelity

Based on repository commit `cc77600`, iOS app version 0.3.1:

- `ios/App/CodexioIOS.swift`: OverviewPage, UsagePage, RecordsPage,
  RequestDetails and MobileSettings. Reuses the four-tab information
  architecture, labels, card grouping, quota presentation and chart series.
- `ios/App/MobileStore.swift`: connection labels.
- `src/codexio/icons/app-light.svg` and `wordmark.svg`: copied intact;
  no redraw, recentering or changed viewBox.

The screens are React recreations of the SwiftUI interface, with synthetic
sample data. This is an interface demonstration, not a device recording.
The current environment did not have an iOS simulator. System controls and
phone hardware are visual approximations. All animation uses the frame clock.
No application source, application version or release assets are changed.

## Storyboard

| Start | Scene | Motion |
| --- | --- | --- |
| 0.0 s | Brand introduction | Phone enters and settles |
| 4.6 s | Overview | Subtle push-in and content scroll |
| 11.2 s | Usage | Three trend lines draw on, model cards scroll |
| 17.8 s | Records | Tap cue and request detail transition |
| 23.4 s | Settings | Light to dark theme dissolve |
| 28.0 s | Closing | Three phones and brand lockup |

Adjacent scenes overlap by 12 frames (0.4 seconds).

## Edit and render

With Node.js and pnpm available:

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm render
```

The entry point is `src/index.ts`. Every scene is available separately in
Remotion Studio under Scenes. Runtime dependencies are locked in
`pnpm-lock.yaml`. Studio can also run using:

```sh
node node_modules/@remotion/cli/remotion-cli.js studio src/index.ts --no-open
```

Render directly with:

```sh
node node_modules/@remotion/cli/remotion-cli.js render src/index.ts Codexio-iOS ../../dev/video/Codexio-iOS-Showcase.mp4 --codec=h264 --crf=18 --concurrency=2
```

Remotion is subject to its own license: https://www.remotion.dev/license
