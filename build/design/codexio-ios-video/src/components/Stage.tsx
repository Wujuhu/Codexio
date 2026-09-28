import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  Interactive,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
export const motion = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
  easing: Easing.bezier(0.16, 1, 0.3, 1),
} as const;
export const Stage: React.FC<{
  children: React.ReactNode;
  chapter?: string;
  dark?: boolean;
}> = ({ children, chapter = "Codexio for iOS", dark = false }) => (
  <AbsoluteFill
    style={{
      background: dark ? "#0e1420" : "#f4f6fa",
      color: dark ? "#fff" : "#151b28",
      fontFamily:
        '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
      overflow: "hidden",
    }}
  >
    <div
      style={{
        position: "absolute",
        width: 1050,
        height: 1050,
        right: -190,
        top: -130,
        borderRadius: "50%",
        background: dark
          ? "radial-gradient(circle,#16365b 0%,#0e142000 70%)"
          : "radial-gradient(circle,#cce5ff 0%,#e6effb70 45%,#f4f6fa00 72%)",
      }}
    />
    <div
      style={{
        position: "absolute",
        left: 106,
        top: 65,
        display: "flex",
        alignItems: "center",
        gap: 14,
      }}
    >
      <Img
        src={staticFile("app-light.svg")}
        style={{ width: 41, height: 41, borderRadius: 10 }}
      />
      <span style={{ fontSize: 23, fontWeight: 600, letterSpacing: 0.5 }}>
        Codexio{" "}
        <span
          style={{
            color: dark ? "#8b9bb0" : "#8390a3",
            fontWeight: 400,
            marginLeft: 7,
          }}
        >
          for iOS
        </span>
      </span>
    </div>
    <div
      style={{
        position: "absolute",
        left: 108,
        bottom: 52,
        right: 108,
        display: "flex",
        justifyContent: "space-between",
        fontSize: 18,
        color: dark ? "#8596ae" : "#8994a5",
        letterSpacing: 1,
      }}
    >
      <span>{chapter}</span>
      <span>界面演示 · 示例数据</span>
    </div>
    {children}
  </AbsoluteFill>
);
export const Copy: React.FC<{
  eyebrow: string;
  lines: string[];
  subtitle: string;
  step: string;
}> = ({ eyebrow, lines, subtitle, step }) => {
  const frame = useCurrentFrame();
  return (
    <Interactive.Div
      name="场景标题"
      style={{
        position: "absolute",
        left: 110,
        top: 272,
        width: 970,
        opacity: interpolate(frame, [8, 30], [0, 1], motion),
        translate: interpolate(frame, [8, 42], ["0px 40px", "0px 0px"], motion),
      }}
    >
      <div
        style={{
          fontSize: 24,
          color: "#087eff",
          fontWeight: 600,
          letterSpacing: 4,
          marginBottom: 30,
        }}
      >
        {step} / {eyebrow}
      </div>
      <h1
        style={{
          fontSize: 104,
          fontWeight: 650,
          letterSpacing: -5,
          lineHeight: 1.2,
          margin: 0,
        }}
      >
        {lines.map((l) => (
          <React.Fragment key={l}>
            {l}
            <br />
          </React.Fragment>
        ))}
      </h1>
      <p
        style={{
          fontSize: 31,
          lineHeight: 1.7,
          color: "#798496",
          marginTop: 33,
          whiteSpace: "pre-line",
        }}
      >
        {subtitle}
      </p>
      <div
        style={{
          height: 4,
          width: 72,
          background: "#087eff",
          borderRadius: 10,
          marginTop: 32,
        }}
      />
    </Interactive.Div>
  );
};
