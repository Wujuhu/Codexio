import React from "react";
import { Img, interpolate, staticFile, useCurrentFrame } from "remotion";

export const blue = "#087eff";
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
type Page = "overview" | "usage" | "records" | "settings";

export const Icon: React.FC<{ name: string; size?: number }> = ({
  name,
  size = 22,
}) => {
  const paths: Record<string, React.ReactNode> = {
    overview: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="2" />
        <rect x="14" y="3" width="7" height="7" rx="2" />
        <rect x="3" y="14" width="7" height="7" rx="2" />
        <rect x="14" y="14" width="7" height="7" rx="2" />
      </>
    ),
    usage: (
      <>
        <path d="M3 3v18h18M6 16l5-6 4 3 6-9" />
      </>
    ),
    records: (
      <>
        <path d="M8 5h13M8 12h13M8 19h13" />
        <circle cx="3" cy="5" r=".7" />
        <circle cx="3" cy="12" r=".7" />
        <circle cx="3" cy="19" r=".7" />
      </>
    ),
    settings: (
      <>
        <path d="M3 5h18M3 12h18M3 19h18" />
        <circle cx="8" cy="5" r="2" fill="currentColor" />
        <circle cx="16" cy="12" r="2" fill="currentColor" />
        <circle cx="9" cy="19" r="2" fill="currentColor" />
      </>
    ),
    laptop: (
      <>
        <rect x="4" y="3" width="16" height="13" rx="2" />
        <path d="M2 19h20M9 19h6" />
      </>
    ),
    qr: (
      <>
        <path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5" />
        <rect x="7" y="7" width="3" height="3" />
        <rect x="14" y="7" width="3" height="3" />
        <path d="M7 14h3v3H7zM14 14h3v3" />
      </>
    ),
    check: <path d="m5 12 4 4L20 5" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 6 6" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 8a8 8 0 1 0 0 8M20 3v6h-6" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name] ?? paths.overview}
    </svg>
  );
};

const Card: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ children, style }) => (
  <div
    style={{
      background: "var(--card)",
      borderRadius: 24,
      padding: 20,
      marginBottom: 16,
      ...style,
    }}
  >
    {children}
  </div>
);
const Row: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ children, style }) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 10,
      ...style,
    }}
  >
    {children}
  </div>
);
const Small: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ fontSize: 12, color: "var(--muted)" }}>{children}</span>
);
const Divider = () => (
  <div style={{ height: 1, background: "var(--line)", margin: "16px 0" }} />
);
const Metrics: React.FC<{ weekly?: boolean }> = ({ weekly = false }) => (
  <Row>
    {[
      ["费用", weekly ? "$86.42" : "$12.36"],
      ["Token", weekly ? "8.62M" : "1.24M"],
      ["请求", weekly ? "286" : "42"],
    ].map(([label, value], i) => (
      <div
        key={label}
        style={{
          flex: 1,
          textAlign: "center",
          borderLeft: i ? "1px solid var(--line)" : undefined,
        }}
      >
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
          {label}
        </div>
        <div
          style={{
            fontSize: 20,
            fontWeight: 650,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </div>
      </div>
    ))}
  </Row>
);
const Quota: React.FC<{ week?: boolean }> = ({ week = false }) => (
  <div>
    <Row>
      <b style={{ fontSize: 17 }}>{week ? "周额度" : "5 小时额度"}</b>
      <b style={{ fontSize: 24 }}>{week ? "64%" : "82%"}</b>
    </Row>
    <div
      style={{
        height: 9,
        background: "#087eff20",
        borderRadius: 10,
        margin: "12px 0 10px",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: week ? "64%" : "82%",
          height: "100%",
          borderRadius: 10,
          background: blue,
        }}
      />
    </div>
    <Row>
      <Small>重置时间</Small>
      <Small>{week ? "10.4 09:00" : "9.28 18:00"}</Small>
    </Row>
  </div>
);
const requests = [
  ["优化首页卡片布局与交互", "gpt-5.4", "high", "$0.84", "84.2K", "14:32"],
  ["为数据同步添加增量缓存", "gpt-5.4", "high", "$1.26", "126.4K", "14:18"],
  ["检查模型费用统计逻辑", "gpt-5.4-mini", "medium", "$0.32", "48.1K", "13:56"],
  ["完善深色模式下的可读性", "gpt-5.4", "high", "$0.68", "68.0K", "13:24"],
  ["整理项目说明文档", "gpt-5.4-mini", "medium", "$0.18", "26.5K", "12:48"],
];
const Request: React.FC<{ index: number }> = ({ index }) => {
  const [title, model, effort, cost, tokens, time] = requests[index];
  return (
    <div style={{ padding: "5px 0" }}>
      <div style={{ fontSize: 15, fontWeight: 550, marginBottom: 9 }}>
        {title}
      </div>
      <Row style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
        <span>{model}</span>
        <span>{cost}</span>
      </Row>
      <Row style={{ fontSize: 11, color: "var(--muted)" }}>
        <span>
          {time} · {effort}
        </span>
        <span>{tokens} Token</span>
      </Row>
    </div>
  );
};

const Overview = () => {
  const frame = useCurrentFrame();
  return (
    <>
      <Row style={{ marginBottom: 13 }}>
        <Img
          src={staticFile("wordmark.svg")}
          style={{
            width: 118,
            height: 49,
            objectFit: "contain",
            filter: "var(--brand-filter)",
          }}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 13,
            background: "var(--card)",
            padding: "8px 11px",
            borderRadius: 22,
          }}
        >
          <Icon name="laptop" size={16} /> MacBook Pro⌄
        </div>
      </Row>
      <Row>
        <h1 style={{ fontSize: 34, margin: 0, letterSpacing: -1 }}>概览</h1>
        <div
          style={{
            display: "flex",
            gap: 5,
            alignItems: "center",
            color: blue,
            fontSize: 14,
            padding: "8px 13px",
            background: "var(--card)",
            borderRadius: 30,
          }}
        >
          <Icon name="refresh" size={14} />
          刷新
        </div>
      </Row>
      <Row style={{ margin: "10px 0 21px" }}>
        <Small>局域网</Small>
        <Small>上次更新：9.28 14:32</Small>
      </Row>
      <Card>
        <Row>
          <b style={{ fontSize: 17 }}>当前任务</b>
          <div
            style={{
              rotate: `${Math.floor(frame / 6) * 45}deg`,
              width: 19,
              height: 19,
              border: "3px dotted #087eff",
              borderRadius: "50%",
            }}
          />
        </Row>
        <div
          style={{
            fontSize: 21,
            fontWeight: 650,
            lineHeight: 1.45,
            margin: "13px 0 9px",
          }}
        >
          优化首页卡片布局与交互
        </div>
        <Small>gpt-5.4 · high</Small>
        <Row style={{ fontSize: 16, marginTop: 15 }}>
          <span>84.2K Token</span>
          <span>$0.84</span>
        </Row>
      </Card>
      <Card>
        <Quota />
        <Divider />
        <Quota week />
      </Card>
      <Card>
        <b style={{ fontSize: 17, display: "block", marginBottom: 18 }}>今天</b>
        <Metrics />
      </Card>
      <h2 style={{ fontSize: 24, margin: "21px 0 15px" }}>最近使用</h2>
      <Card>
        <Request index={0} />
        <Divider />
        <Request index={1} />
        <Divider />
        <Request index={2} />
      </Card>
    </>
  );
};

const Usage = () => {
  const frame = useCurrentFrame();
  const reveal = interpolate(frame, [18, 85], [0, 1], clamp);
  return (
    <>
      <h1 style={{ fontSize: 34, margin: "10px 0 24px" }}>用量</h1>
      <Segments labels={["近 7 天", "近 30 天", "近 90 天"]} />
      <Card>
        <Metrics weekly />
        <div
          style={{
            display: "flex",
            gap: 18,
            fontSize: 12,
            color: "var(--muted)",
            margin: "24px 0 8px",
          }}
        >
          {[
            ["费用", blue],
            ["Token", "#19afa7"],
            ["请求", "#a451e8"],
          ].map(([t, c]) => (
            <span
              key={t}
              style={{ display: "flex", gap: 5, alignItems: "center" }}
            >
              <i
                style={{ width: 6, height: 6, borderRadius: 6, background: c }}
              />
              {t}
            </span>
          ))}
        </div>
        <svg
          viewBox="0 0 310 220"
          style={{ width: "100%", height: 210, overflow: "visible" }}
        >
          <defs>
            <clipPath id="chart-clip">
              <rect width={310 * reveal} height="220" />
            </clipPath>
          </defs>
          {[40, 90, 140, 190].map((y) => (
            <path
              key={y}
              d={`M0 ${y}H310`}
              stroke="var(--line)"
              strokeDasharray="3 5"
            />
          ))}
          <g clipPath="url(#chart-clip)">
            <path
              d="M0 165L50 140L100 156L155 68L205 96L258 36L310 62"
              stroke={blue}
              strokeWidth="3"
              fill="none"
            />
            <path
              d="M0 184L50 156L100 127L155 95L205 118L258 63L310 36"
              stroke="#19afa7"
              strokeWidth="2.5"
              strokeDasharray="7 4"
              fill="none"
            />
            <path
              d="M0 147L50 174L100 149L155 114L205 74L258 99L310 52"
              stroke="#a451e8"
              strokeWidth="2.5"
              strokeDasharray="2 4"
              fill="none"
            />
          </g>
          <g fill="var(--muted)" fontSize="11">
            <text x="0" y="216">
              9月22日
            </text>
            <text x="132" y="216">
              9月25日
            </text>
            <text x="265" y="216">
              9月28日
            </text>
          </g>
        </svg>
        <Small>9月28日 · $12.36 / 1.24M Token / 42 次</Small>
      </Card>
      <h2 style={{ fontSize: 24, margin: "24px 0 16px" }}>模型占比</h2>
      <Segments labels={["费用", "Token", "请求"]} />
      {[
        ["gpt-5.4", "$64.82", 75, 72, 65],
        ["gpt-5.4-mini", "$21.60", 25, 28, 35],
      ].map(([name, cost, a, b, c]) => (
        <Card key={name}>
          <Row>
            <b style={{ fontSize: 17 }}>{name}</b>
            <span style={{ fontSize: 14 }}>{cost}</span>
          </Row>
          <div
            style={{
              height: 8,
              background: "#087eff20",
              borderRadius: 5,
              margin: "16px 0",
            }}
          >
            <div
              style={{
                width: `${a}%`,
                height: "100%",
                borderRadius: 5,
                background: blue,
              }}
            />
          </div>
          <Row style={{ fontSize: 12, color: "var(--muted)" }}>
            <span>费用 {a}%</span>
            <span>Token {b}%</span>
            <span>请求 {c}%</span>
          </Row>
        </Card>
      ))}
    </>
  );
};
const Segments: React.FC<{ labels: string[] }> = ({ labels }) => (
  <div
    style={{
      display: "flex",
      padding: 3,
      borderRadius: 9,
      background: "var(--segment)",
      marginBottom: 20,
    }}
  >
    {labels.map((s, i) => (
      <div
        key={s}
        style={{
          flex: 1,
          textAlign: "center",
          fontSize: 13,
          padding: "7px 0",
          borderRadius: 7,
          background: i === 0 ? "var(--card)" : undefined,
          boxShadow: i === 0 ? "0 1px 5px #00000015" : undefined,
          fontWeight: i === 0 ? 600 : 400,
        }}
      >
        {s}
      </div>
    ))}
  </div>
);
const Records: React.FC<{ details: boolean }> = ({ details }) => (
  <>
    {details ? (
      <>
        <div style={{ color: blue, fontSize: 17, margin: "14px 0 36px" }}>
          ‹ 记录{" "}
          <span
            style={{ color: "var(--ink)", marginLeft: 104, fontWeight: 600 }}
          >
            请求
          </span>
        </div>
        <Card>
          <div style={{ fontSize: 17, lineHeight: 1.6 }}>
            优化首页卡片布局与交互
          </div>
        </Card>
        <Card>
          {[
            ["模型", "gpt-5.4"],
            ["状态", "已完成"],
            ["思考强度", "high"],
            ["费用", "$0.84"],
            ["Token", "84.2K"],
            ["耗时", "38 秒"],
          ].map(([l, v], i) => (
            <React.Fragment key={l}>
              {i > 0 && <Divider />}
              <Row style={{ fontSize: 16, padding: "2px 0" }}>
                <span>{l}</span>
                <span style={{ color: "var(--muted)" }}>{v}</span>
              </Row>
            </React.Fragment>
          ))}
        </Card>
      </>
    ) : (
      <>
        <h1 style={{ fontSize: 34, margin: "10px 0 24px" }}>记录</h1>
        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            background: "var(--segment)",
            color: "var(--muted)",
            borderRadius: 12,
            padding: "11px 13px",
            fontSize: 16,
            marginBottom: 24,
          }}
        >
          <Icon name="search" size={18} />
          搜索请求或模型
        </div>
        <Card style={{ padding: "12px 18px" }}>
          {requests.map((r, i) => (
            <React.Fragment key={r[0]}>
              {i > 0 && <Divider />}
              <Row>
                <div style={{ flex: 1 }}>
                  <Request index={i} />
                </div>
                <span style={{ color: "#b5b5bb" }}>
                  <Icon name="chevron" size={14} />
                </span>
              </Row>
            </React.Fragment>
          ))}
        </Card>
      </>
    )}
  </>
);
const Settings: React.FC<{ dark: boolean }> = ({ dark }) => (
  <>
    <h1 style={{ fontSize: 34, margin: "10px 0 34px" }}>设置</h1>
    <div style={{ margin: "0 18px 9px", fontSize: 13, color: "var(--muted)" }}>
      我的 Mac
    </div>
    <Card>
      <Row>
        <span
          style={{
            display: "flex",
            gap: 10,
            alignItems: "center",
            fontSize: 16,
          }}
        >
          <Icon name="laptop" />
          MacBook Pro
        </span>
        <span style={{ color: blue }}>
          <Icon name="check" size={19} />
        </span>
      </Row>
      <Divider />
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          color: blue,
          fontSize: 16,
        }}
      >
        <Icon name="qr" />
        添加 Mac
      </div>
    </Card>
    <div
      style={{ margin: "28px 18px 9px", fontSize: 13, color: "var(--muted)" }}
    >
      外观
    </div>
    <Card>
      <Row style={{ fontSize: 16 }}>
        <span>主题</span>
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            color: "var(--muted)",
          }}
        >
          {dark ? "深色" : "跟随系统"} <span style={{ fontSize: 13 }}>⌃⌄</span>
        </span>
      </Row>
    </Card>
    <div
      style={{ margin: "28px 18px 9px", fontSize: 13, color: "var(--muted)" }}
    >
      此 iPhone
    </div>
    <Card>
      <span style={{ fontSize: 16 }}>我的 iPhone</span>
    </Card>
    <div
      style={{ margin: "28px 18px 9px", fontSize: 13, color: "var(--muted)" }}
    >
      连接
    </div>
    <Card>
      <Row style={{ fontSize: 16 }}>
        <span>当前连接</span>
        <Small>局域网</Small>
      </Row>
      <Divider />
      <span style={{ fontSize: 16, color: blue }}>立即刷新</span>
    </Card>
    <Card style={{ marginTop: 28 }}>
      <span style={{ fontSize: 16, color: "#ff453a" }}>移除此 Mac</span>
    </Card>
  </>
);

export const Phone: React.FC<{
  page?: Page;
  dark?: boolean;
  scale?: number;
  x?: number;
  y?: number;
  rotation?: number;
  details?: boolean;
  scroll?: number;
}> = ({
  page = "overview",
  dark = false,
  scale = 1,
  x = 0,
  y = 0,
  rotation = 0,
  details = false,
  scroll = 0,
}) => {
  const palette = {
    "--card": dark ? "#1c1c1e" : "#fff",
    "--ink": dark ? "#f5f5f7" : "#141419",
    "--muted": dark ? "#98989f" : "#808089",
    "--line": dark ? "#353539" : "#e7e7eb",
    "--segment": dark ? "#2c2c30" : "#e5e5eb",
    "--brand-filter": dark ? "invert(1)" : "none",
  } as React.CSSProperties;
  return (
    <div
      style={{
        position: "absolute",
        width: 414,
        height: 878,
        left: x,
        top: y,
        scale,
        rotate: `${rotation}deg`,
        transformOrigin: "center",
        borderRadius: 63,
        padding: 11,
        background:
          "linear-gradient(130deg,#90939a,#202227 25%,#676b73 58%,#121316)",
        boxShadow: "0 48px 65px -28px #25335755, 0 0 0 1px #6a6b70",
        ...palette,
      }}
    >
      <div
        style={{
          position: "absolute",
          left: -3,
          top: 175,
          width: 4,
          height: 61,
          background: "#5e6269",
          borderRadius: 4,
        }}
      />
      <div
        style={{
          position: "absolute",
          right: -3,
          top: 213,
          width: 4,
          height: 83,
          background: "#5e6269",
          borderRadius: 4,
        }}
      />
      <div
        style={{
          width: "100%",
          height: "100%",
          borderRadius: 53,
          overflow: "hidden",
          position: "relative",
          background: dark ? "#000" : "#f2f2f7",
          color: "var(--ink)",
          border: "2px solid #08090b",
        }}
      >
        <div
          style={{
            height: 59,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 28px",
            fontSize: 15,
            fontWeight: 650,
          }}
        >
          <span>9:41</span>
          <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
            <svg width="17" height="13" viewBox="0 0 17 13" fill="currentColor">
              {[5, 7, 10, 13].map((h, i) => (
                <rect
                  key={h}
                  x={i * 4.5}
                  y={13 - h}
                  width="3"
                  height={h}
                  rx=".7"
                />
              ))}
            </svg>
            <svg
              width="17"
              height="13"
              viewBox="0 0 17 13"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M1 4q7-7 15 0M4 7q4-4 9 0M7 10q1-1 3 0" />
            </svg>
            <div
              style={{
                width: 23,
                height: 11,
                border: "1.2px solid currentColor",
                borderRadius: 3,
                padding: 1,
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: "82%",
                  background: "currentColor",
                  borderRadius: 1,
                }}
              />
            </div>
          </div>
        </div>
        <div
          style={{
            position: "absolute",
            top: 12,
            left: 133,
            width: 126,
            height: 34,
            borderRadius: 28,
            background: "#000",
          }}
        />
        <div
          style={{
            position: "absolute",
            top: 62,
            bottom: 85,
            left: 0,
            right: 0,
            overflow: "hidden",
          }}
        >
          <div
            style={{ padding: "0 20px 20px", translate: `0px ${-scroll}px` }}
          >
            {page === "overview" ? (
              <Overview />
            ) : page === "usage" ? (
              <Usage />
            ) : page === "records" ? (
              <Records details={details} />
            ) : (
              <Settings dark={dark} />
            )}
          </div>
        </div>
        <div
          style={{
            position: "absolute",
            left: 10,
            right: 10,
            bottom: 21,
            height: 64,
            borderRadius: 35,
            border: dark ? "1px solid #ffffff19" : "1px solid #ffffff",
            background: dark ? "#262629ed" : "#fafafbee",
            boxShadow: "0 3px 18px #00000013",
            display: "flex",
            alignItems: "center",
            padding: 4,
          }}
        >
          {(["overview", "usage", "records", "settings"] as Page[]).map(
            (p, i) => (
              <div
                key={p}
                style={{
                  flex: 1,
                  height: 55,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 3,
                  color: p === page ? blue : "var(--muted)",
                  background:
                    p === page ? (dark ? "#3a3a41" : "#e8e8ed") : undefined,
                  borderRadius: 28,
                }}
              >
                <Icon name={p} size={22} />
                <span style={{ fontSize: 10, fontWeight: 600 }}>
                  {["概览", "用量", "记录", "设置"][i]}
                </span>
              </div>
            ),
          )}
        </div>
        <div
          style={{
            position: "absolute",
            width: 131,
            height: 5,
            borderRadius: 8,
            background: dark ? "#ddd" : "#151518",
            bottom: 8,
            left: 129,
          }}
        />
      </div>
    </div>
  );
};
