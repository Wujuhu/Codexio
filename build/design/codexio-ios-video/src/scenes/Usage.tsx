import { Interactive, interpolate, useCurrentFrame } from "remotion";
import { Phone } from "../components/Phone";
import { Copy, motion, Stage } from "../components/Stage";
export const UsageScene = () => {
  const frame = useCurrentFrame();
  return (
    <Stage chapter="02 / 用量">
      <Copy
        step="02"
        eyebrow="趋势 · 模型占比"
        lines={["每一份用量，", "都有迹可循。"]}
        subtitle={
          "费用、Token、请求趋势同屏对照。\n按 7 / 30 / 90 天查看使用变化。"
        }
      />
      <Interactive.Div
        name="用量手机"
        style={{
          position: "absolute",
          left: 1234,
          top: 110,
          width: 414,
          height: 878,
          translate: interpolate(
            frame,
            [0, 42],
            ["80px 0px", "0px 0px"],
            motion,
          ),
        }}
      >
        <Phone
          page="usage"
          scroll={interpolate(frame, [124, 181], [0, 90], motion)}
        />
      </Interactive.Div>
      <Interactive.Div
        name="趋势图例"
        style={{
          position: "absolute",
          left: 110,
          top: 788,
          display: "flex",
          gap: 38,
          opacity: interpolate(frame, [35, 58], [0, 1], motion),
        }}
      >
        {[
          ["费用", "#087eff"],
          ["Token", "#19afa7"],
          ["请求", "#a451e8"],
        ].map(([label, color]) => (
          <div
            key={label}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontSize: 24,
              color: "#64738a",
            }}
          >
            <div
              style={{
                width: 30,
                height: 4,
                borderRadius: 4,
                background: color,
              }}
            />
            {label}
          </div>
        ))}
      </Interactive.Div>
    </Stage>
  );
};
