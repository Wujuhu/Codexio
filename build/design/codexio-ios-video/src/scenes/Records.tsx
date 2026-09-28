import { Interactive, interpolate, useCurrentFrame } from "remotion";
import { Phone } from "../components/Phone";
import { Copy, motion, Stage } from "../components/Stage";
export const RecordsScene = () => {
  const frame = useCurrentFrame();
  return (
    <Stage chapter="03 / 记录">
      <Copy
        step="03"
        eyebrow="请求记录 · 按需查看"
        lines={["找到那次请求，", "看清每个细节。"]}
        subtitle={"搜索请求或模型，展开查看\n思考强度、费用、Token 与耗时。"}
      />
      <Interactive.Div
        name="记录列表"
        style={{
          position: "absolute",
          left: 1234,
          top: 110,
          width: 414,
          height: 878,
          opacity: interpolate(frame, [84, 102], [1, 0], motion),
          translate: interpolate(
            frame,
            [84, 110],
            ["0px 0px", "-45px 0px"],
            motion,
          ),
        }}
      >
        <Phone page="records" />
      </Interactive.Div>
      <Interactive.Div
        name="请求详情"
        style={{
          position: "absolute",
          left: 1234,
          top: 110,
          width: 414,
          height: 878,
          opacity: interpolate(frame, [84, 108], [0, 1], motion),
          translate: interpolate(
            frame,
            [84, 115],
            ["70px 0px", "0px 0px"],
            motion,
          ),
        }}
      >
        <Phone page="records" details />
      </Interactive.Div>
      <Interactive.Div
        name="点击提示"
        style={{
          position: "absolute",
          left: 1570,
          top: 382,
          width: 51,
          height: 51,
          borderRadius: "50%",
          border: "2px solid #087eff",
          background: "#087eff20",
          opacity: interpolate(
            frame,
            [62, 72, 83, 95],
            [0, 0.9, 0.9, 0],
            motion,
          ),
          scale: interpolate(frame, [62, 95], [1.5, 0.6], motion),
        }}
      />
    </Stage>
  );
};
