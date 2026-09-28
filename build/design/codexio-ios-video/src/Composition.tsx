import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { Intro } from "./scenes/Intro";
import { OverviewScene } from "./scenes/Overview";
import { UsageScene } from "./scenes/Usage";
import { RecordsScene } from "./scenes/Records";
import { SettingsScene } from "./scenes/Settings";
import { Outro } from "./scenes/Outro";
export const Showcase = () => (
  <TransitionSeries>
    <TransitionSeries.Sequence durationInFrames={150} name="Intro">
      <Intro />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition
      presentation={fade()}
      timing={linearTiming({ durationInFrames: 12 })}
    />
    <TransitionSeries.Sequence durationInFrames={210} name="Overview">
      <OverviewScene />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition
      presentation={fade()}
      timing={linearTiming({ durationInFrames: 12 })}
    />
    <TransitionSeries.Sequence durationInFrames={210} name="Usage">
      <UsageScene />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition
      presentation={fade()}
      timing={linearTiming({ durationInFrames: 12 })}
    />
    <TransitionSeries.Sequence durationInFrames={180} name="Records">
      <RecordsScene />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition
      presentation={fade()}
      timing={linearTiming({ durationInFrames: 12 })}
    />
    <TransitionSeries.Sequence durationInFrames={150} name="Settings">
      <SettingsScene />
    </TransitionSeries.Sequence>
    <TransitionSeries.Transition
      presentation={fade()}
      timing={linearTiming({ durationInFrames: 12 })}
    />
    <TransitionSeries.Sequence durationInFrames={120} name="Outro">
      <Outro />
    </TransitionSeries.Sequence>
  </TransitionSeries>
);
