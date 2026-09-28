import { Interactive, interpolate, useCurrentFrame } from "remotion";
import { Phone } from "../components/Phone";
import { Copy, motion, Stage } from "../components/Stage";
export const SettingsScene = () => {
  const frame = useCurrentFrame();
  return (
    <Stage chapter="04 / 设置">
      <Copy
        step="04"
        eyebrow="我的 Mac · 外观"
        lines={["连接你的 Mac，", "切换你的风格。"]}
        subtitle={"扫码添加 Mac，查看当前连接。\n浅色、深色，或跟随系统。"}
      />
      <Phone page="settings" x={1234} y={110} />
      <Interactive.Div
        name="深色外观"
        style={{
          position: "absolute",
          left: 1234,
          top: 110,
          width: 414,
          height: 878,
          opacity: interpolate(frame, [65, 98], [0, 1], motion),
        }}
      >
        <Phone page="settings" dark />
      </Interactive.Div>
      <Interactive.Div
        name="主题切换"
        style={{
          position: "absolute",
          left: 110,
          top: 795,
          display: "flex",
          gap: 13,
          opacity: interpolate(frame, [15, 35], [0, 1], motion),
        }}
      >
        <div
          style={{
            height: 46,
            width: 46,
            borderRadius: "50%",
            background: "#fff",
            border: "1px solid #d8dce3",
          }}
        />
        <div
          style={{
            height: 46,
            width: 46,
            borderRadius: "50%",
            background: "#191d26",
            boxShadow: frame > 80 ? "0 0 0 5px #087eff25" : "none",
          }}
        />
      </Interactive.Div>
    </Stage>
  );
};
