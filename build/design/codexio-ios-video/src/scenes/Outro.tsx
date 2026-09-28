import {
  Img,
  Interactive,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { Phone } from "../components/Phone";
import { motion, Stage } from "../components/Stage";
export const Outro = () => {
  const frame = useCurrentFrame();
  return (
    <Stage chapter="Codexio for iOS">
      <Interactive.Div
        name="界面收束"
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: 1920,
          height: 1080,
          opacity: interpolate(frame, [0, 30], [0, 1], motion),
        }}
      >
        <Phone page="usage" x={1032} y={175} scale={0.78} rotation={-9} />
        <Phone
          page="settings"
          dark
          x={1490}
          y={176}
          scale={0.78}
          rotation={9}
        />
        <Phone x={1262} y={112} scale={0.94} />
      </Interactive.Div>
      <Interactive.Div
        name="片尾品牌"
        style={{
          position: "absolute",
          left: 110,
          top: 245,
          opacity: interpolate(frame, [5, 35], [0, 1], motion),
          translate: interpolate(
            frame,
            [5, 40],
            ["0px 30px", "0px 0px"],
            motion,
          ),
        }}
      >
        <Img
          src={staticFile("app-light.svg")}
          style={{
            width: 125,
            height: 125,
            borderRadius: 29,
            boxShadow: "0 16px 40px #26364d12",
          }}
        />
        <Img
          src={staticFile("wordmark.svg")}
          style={{
            display: "block",
            width: 630,
            height: 240,
            objectFit: "contain",
            marginLeft: -50,
            marginTop: 5,
          }}
        />
        <div
          style={{
            fontSize: 55,
            fontWeight: 600,
            marginTop: -10,
            letterSpacing: -1,
          }}
        >
          离开桌面，也能一眼掌握。
        </div>
        <div
          style={{
            fontSize: 26,
            color: "#798496",
            marginTop: 27,
            letterSpacing: 3,
          }}
        >
          概览 / 用量 / 记录 / 设置
        </div>
      </Interactive.Div>
    </Stage>
  );
};
