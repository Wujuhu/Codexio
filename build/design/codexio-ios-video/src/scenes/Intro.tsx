import {
  Img,
  Interactive,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { Phone } from "../components/Phone";
import { motion, Stage } from "../components/Stage";
export const Intro = () => {
  const frame = useCurrentFrame();
  return (
    <Stage chapter="掌上查看 · 随时了解">
      <Interactive.Div
        name="品牌开场"
        style={{
          position: "absolute",
          left: 110,
          top: 249,
          opacity: interpolate(frame, [0, 24], [0, 1], motion),
          translate: interpolate(
            frame,
            [0, 45],
            ["0px 50px", "0px 0px"],
            motion,
          ),
        }}
      >
        <div
          style={{
            fontSize: 25,
            color: "#087eff",
            letterSpacing: 5,
            marginBottom: 12,
          }}
        >
          YOUR CODEX. AT A GLANCE.
        </div>
        <Img
          src={staticFile("wordmark.svg")}
          style={{
            width: 740,
            height: 300,
            objectFit: "contain",
            marginLeft: -57,
          }}
        />
        <h1
          style={{
            fontSize: 68,
            fontWeight: 600,
            letterSpacing: -2,
            margin: "-24px 0 26px",
          }}
        >
          Codex 动态，尽在掌中。
        </h1>
        <p style={{ fontSize: 31, color: "#798496", lineHeight: 1.6 }}>
          任务、额度与用量
          <br />
          打开 iPhone，一目了然。
        </p>
      </Interactive.Div>
      <Interactive.Div
        name="手机入场"
        style={{
          position: "absolute",
          left: 1250,
          top: 114,
          width: 414,
          height: 878,
          translate: interpolate(
            frame,
            [0, 55],
            ["160px 65px", "0px 0px"],
            motion,
          ),
          opacity: interpolate(frame, [8, 40], [0, 1], motion),
          rotate: interpolate(frame, [0, 70], ["9deg", "-4deg"], motion),
          scale: interpolate(frame, [0, 85], [0.9, 1], motion),
        }}
      >
        <Phone />
      </Interactive.Div>
    </Stage>
  );
};
