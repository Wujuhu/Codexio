import "./index.css";
import { Composition, Folder } from "remotion";
import { Showcase } from "./Composition";
import { Intro } from "./scenes/Intro";
import { OverviewScene } from "./scenes/Overview";
import { UsageScene } from "./scenes/Usage";
import { RecordsScene } from "./scenes/Records";
import { SettingsScene } from "./scenes/Settings";
import { Outro } from "./scenes/Outro";
export const RemotionRoot = () => (
  <>
    <Composition
      id="Codexio-iOS"
      component={Showcase}
      durationInFrames={960}
      fps={30}
      width={1920}
      height={1080}
    />
    <Folder name="Scenes">
      <Composition
        id="Intro"
        component={Intro}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="Overview"
        component={OverviewScene}
        durationInFrames={210}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="Usage"
        component={UsageScene}
        durationInFrames={210}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="Records"
        component={RecordsScene}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="Settings"
        component={SettingsScene}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
      />
      <Composition
        id="Outro"
        component={Outro}
        durationInFrames={120}
        fps={30}
        width={1920}
        height={1080}
      />
    </Folder>
  </>
);
