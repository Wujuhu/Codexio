import { Interactive, interpolate, useCurrentFrame } from "remotion";
import { Phone } from "../components/Phone";
import { Copy, motion, Stage } from "../components/Stage";
export const OverviewScene = () => {
  const frame = useCurrentFrame();
  return (
    <Stage chapter="01 / 概览">
      <Copy
        step="01"
        eyebrow="当前任务 · 剩余额度"
        lines={["正在做什么，", "还有多少额度。"]}
        subtitle={"模型、思考强度、Token 与费用。\n5 小时和周额度，同屏查看。"}
      />
      <Interactive.Div
        name="概览手机"
        style={{
          position: "absolute",
          left: 1234,
          top: 110,
          width: 414,
          height: 878,
          translate: interpolate(
            frame,
            [0, 40],
            ["80px 0px", "0px 0px"],
            motion,
          ),
          scale: interpolate(frame, [20, 190], [1, 1.035], motion),
        }}
      >
        <Phone scroll={interpolate(frame, [118, 177], [0, 116], motion)} />
      </Interactive.Div>
      <Interactive.Div
        name="额度重点"
        style={{
          position: "absolute",
          left: 800,
          top: 773,
          padding: "19px 27px",
          borderRadius: 24,
          background: "#fff",
          boxShadow: "0 14px 45px #30508312",
          opacity: interpolate(frame, [35, 62], [0, 1], motion),
          translate: interpolate(
            frame,
            [35, 75],
            ["0px 25px", "0px 0px"],
            motion,
          ),
        }}
      >
        <div style={{ fontSize: 18, color: "#798496", marginBottom: 5 }}>
          周额度剩余
        </div>
        <span style={{ fontSize: 48, fontWeight: 650, color: "#087eff" }}>
          64<span style={{ fontSize: 28 }}>%</span>
        </span>
      </Interactive.Div>
    </Stage>
  );
};
