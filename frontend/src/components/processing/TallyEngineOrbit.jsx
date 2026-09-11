import "./TallyEngineOrbit.css";
import tallyCubeImg from "../../assets/tally-engine-cube.png";

/* ------------------------------------------------------------------ *
 * Fixed visual slots — tuned to match the reference design pixel-for-
 * pixel. Node DATA (label/caption/status) comes from props; node
 * POSITION/ICON-SHAPE stays fixed here so the diagram never drifts
 * from the reference layout no matter what ProcessingStage sends.
 * ------------------------------------------------------------------ */
// Angles (degrees) for the small chevron ticks around the center "tech
// ring" -- evenly spaced so the ring reads as an arrowed collar rather
// than a plain dashed circle.
const CHEVRON_ANGLES = Array.from({ length: 10 }, (_, i) => i * 36);

const SLOTS = {
  source:  { cx: 441,  cy: 218, r: 46, icon: "doc",      side: "left"   },
  party:   { cx: 983,  cy: 213, r: 46, icon: "users",    side: "right"  },
  tally:   { cx: 376,  cy: 527, r: 58, icon: "cube",     side: "left", square: true },
  masters: { cx: 1289, cy: 480, r: 46, icon: "layers",   side: "right"  },
  license: { cx: 510,  cy: 633, r: 46, icon: "shield",   side: "bottom" },
  verify:  { cx: 1000, cy: 633, r: 46, icon: "building", side: "bottom" },
};

function NodeIcon({ type }) {
  switch (type) {
    case "doc":
      return (
        <g stroke="currentColor" strokeWidth="1.8" fill="none">
          <path d="M4 2h16a2 2 0 0 1 2 2v3l-6 0v18H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" transform="scale(1.05)" />
          <path d="M14 2v6h6" transform="scale(1.05)" />
        </g>
      );
    case "users":
      return (
        <g stroke="currentColor" strokeWidth="1.7" fill="none">
          <circle cx="13" cy="8" r="4.5" />
          <path d="M4 24c0-5 4-8.5 9-8.5s9 3.5 9 8.5" />
          <circle cx="23" cy="10" r="3.3" />
          <path d="M21 15.5c4 .4 7.5 3.5 7.5 8.5" />
        </g>
      );
    case "layers":
      return (
        <g stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinejoin="round">
          <polygon points="22,2 42,12 22,22 2,12" />
          <polygon points="2,20 22,30 42,20" opacity=".8" />
          <polygon points="2,28 22,38 42,28" opacity=".55" />
        </g>
      );
    case "shield":
      return (
        <g stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinejoin="round">
          <path d="M20 2 L38 9 V22 C38 33 30 41 20 46 C10 41 2 33 2 22 V9 Z" />
          <path d="M12 22 l6 6 l12 -13" strokeLinecap="round" />
        </g>
      );
    case "cube":
      return (
        <g stroke="#ffffff" strokeWidth="2" fill="none" strokeLinejoin="round">
          <polygon points="20,4 36,12 20,20 4,12" />
          <polygon points="4,12 20,20 20,36 4,28" />
          <polygon points="36,12 20,20 20,36 36,28" />
        </g>
      );
    case "building":
      // Same glyph as the left-hand prep-timeline's "company" icon (24x24
      // viewBox), scaled up to match this diagram's ~40-unit icon boxes.
      return (
        <g stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" transform="scale(1.7)">
          <path d="M4 21V7l8-4 8 4v14" />
          <path d="M9 21v-6h6v6" />
          <path d="M9 11h.01M15 11h.01M9 15h.01M15 15h.01" />
        </g>
      );
    case "plug":
      // Same glyph as the left-hand prep-timeline's "connection" icon --
      // used here for the Tally System node once it has actually failed.
      return (
        <g stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" transform="scale(1.7)">
          <path d="M9 2v4M15 2v4" />
          <path d="M7 8h10l-1 6a4 4 0 0 1-4 4h0a4 4 0 0 1-4-4z" />
          <path d="M12 18v4" />
        </g>
      );
    default:
      return null;
  }
}

export default function TallyEngineOrbit({
  status = "live",           // "live" | "complete"
  heading = "LIVE PROCESSING",
  subheading = "Your data is being processed securely in the background.",
  coreLabel = "Tally Engine",
  nodes = [],
  operation = { title: "Idle", detail: "No background operation is currently running." },
  eta = "Just a few seconds",
  security = "256-bit encrypted transfer",
  className = "",
  // When true, renders only the orbit SVG itself -- no heading/subtitle/
  // bottom bar and no white ".teo-panel" card chrome -- for embedding inside
  // a host page that already renders its own live-status header and
  // current-operation bar (e.g. GstTallyImport's PartyScreen).
  bare = false,
}) {
  const complete = status === "complete";
  const theme = complete ? "green" : "blue";

  const orbitSvg = (
        <div className="teo-orbit-wrap">
          <svg viewBox="0 0 1536 780" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <linearGradient id="teo-talSq" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#4a8bf0" /><stop offset="1" stopColor="#2451c9" />
              </linearGradient>

              <radialGradient id="teo-glowGrad-blue" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="rgba(180,215,255,1)" />
                <stop offset="45%" stopColor="rgba(150,200,255,0.55)" />
                <stop offset="100%" stopColor="rgba(150,200,255,0)" />
              </radialGradient>
              <radialGradient id="teo-glowCore-blue" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="rgba(255,255,255,1)" />
                <stop offset="55%" stopColor="rgba(210,235,255,0.65)" />
                <stop offset="100%" stopColor="rgba(210,235,255,0)" />
              </radialGradient>

              <radialGradient id="teo-glowGrad-green" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="rgba(175,230,195,1)" />
                <stop offset="45%" stopColor="rgba(160,220,185,0.55)" />
                <stop offset="100%" stopColor="rgba(160,220,185,0)" />
              </radialGradient>
              <radialGradient id="teo-glowCore-green" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="rgba(255,255,255,1)" />
                <stop offset="55%" stopColor="rgba(215,245,225,0.65)" />
                <stop offset="100%" stopColor="rgba(215,245,225,0)" />
              </radialGradient>

              <filter id="teo-softBlur" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="6" />
              </filter>

              {/* Clips the reference cube photo to a circle (in its own
                  bounding-box space) so the screenshot's rectangular corners
                  never show past the glow rings around it. */}
              <clipPath id="teo-cube-photo-clip" clipPathUnits="objectBoundingBox">
                <circle cx="0.5" cy="0.5" r="0.5" />
              </clipPath>
            </defs>

            {/* outer big dashed ring -- kept faint, it's the secondary circle */}
            <circle className="teo-spin-slow" cx="755" cy="420" r="345" fill="none" stroke={complete ? "#cdeed9" : "#cddaf3"} strokeWidth="1.6" strokeDasharray="3 7" />
            {/* inner solid ring -- the prominent "outer circle" that reads
                clearly against the panel, matching the reference's visible
                blue ring threading past the corner nodes (vs. the barely-
                there pale version this used to be). */}
            <circle className="teo-spin-slow-rev" cx="755" cy="420" r="255" fill="none" stroke={complete ? "#7fd0a0" : "#7fa8f2"} strokeWidth="2" opacity=".8" />

            {/* connectors */}
            <path d="M 355,555 C 480,690 620,715 755,712 C 900,708 1000,690 1050,645" fill="none" stroke={complete ? "#7fd0a0" : "#7fa8f2"} strokeWidth="2.5" />
            <path d="M 1050,645 C 1100,600 1180,545 1250,500" fill="none" stroke={complete ? "#7fd0a0" : "#7fa8f2"} strokeWidth="2.5" />
            <path d="M 405,255 C 500,225 620,200 700,193 C 780,188 900,200 970,240" fill="none" stroke={complete ? "#a9dec0" : "#a9bdec"} strokeWidth="1.6" strokeDasharray="3 6" />
            <path d="M 1000,255 C 1065,320 1092,378 1090,420 C 1088,455 1155,470 1240,480" fill="none" stroke={complete ? "#9fd6b6" : "#b79bf0"} strokeWidth="1.6" strokeDasharray="3 6" />
            <path d="M 1035,605 C 1090,560 1150,520 1245,492" fill="none" stroke={complete ? "#9fd6b6" : "#b79bf0"} strokeWidth="1.6" strokeDasharray="3 6" />

            {/* static accent dots */}
            <circle className="teo-pulse-node" cx="700" cy="155" r="6" fill={complete ? "#2fa864" : "#2f6fed"} />
            <circle className="teo-pulse-node" cx="445" cy="383" r="6" fill={complete ? "#2fa864" : "#2f6fed"} />
            <circle className="teo-pulse-node" cx="1060" cy="383" r="6" fill={complete ? "#5fc98a" : "#7c5cff"} />
            <circle className="teo-pulse-node" cx="785" cy="700" r="6" fill={complete ? "#2fa864" : "#2f6fed"} />
            <circle className="teo-pulse-node" cx="585" cy="600" r="5" fill={complete ? "#6fe0b0" : "#6fd0ff"} />

            {!complete && (
              <>
                <circle r="5" fill="#4f86f2">
                  <animateMotion dur="3.2s" repeatCount="indefinite" path="M 355,555 C 480,690 620,715 755,712 C 900,708 1000,690 1050,645 C 1100,600 1180,545 1250,500" />
                </circle>
                <circle r="4.5" fill="#8a7bf0">
                  <animateMotion dur="2.8s" repeatCount="indefinite" path="M 405,255 C 500,225 620,200 700,193 C 780,188 900,200 970,240" />
                </circle>
                <circle r="4.5" fill="#8a7bf0">
                  <animateMotion dur="3.4s" repeatCount="indefinite" path="M 1000,255 C 1065,320 1092,378 1090,420 C 1088,455 1155,470 1240,480" />
                </circle>
                <circle r="6" fill="#2f6fed">
                  <animateMotion dur="6s" repeatCount="indefinite" path="M 755,420 m -160,0 a 160,160 0 1,0 320,0 a 160,160 0 1,0 -320,0" />
                </circle>
                <circle r="5" fill="#7c5cff">
                  <animateMotion dur="8s" repeatCount="indefinite" path="M 755,420 m -120,0 a 120,120 0 1,1 240,0 a 120,120 0 1,1 -240,0" />
                </circle>
              </>
            )}

            {/* ---- data-driven satellite nodes ---- */}
            {nodes.map((n) => {
              const slot = SLOTS[n.id];
              if (!slot) return null;
              const { cx, cy, r, side, square } = slot;
              const done = n.status === "done";
              const active = n.status === "active";
              const failed = n.status === "failed";
              const subClass = done ? "green" : active ? "blue" : failed ? "red" : "gray";
              // The square "engine" node (Tally System) only shows its blue
              // cube glyph while the connection hasn't failed -- a failed
              // connection drops to the same plain red circle every other
              // failed node uses, with a plug icon instead of the cube.
              const showSquare = square && !failed;

              // label box placement per side
              let foX, foY, foW = 250, foH = active ? 110 : 70, align = "left";
              if (side === "left") { foX = cx - r - 275; foY = cy - 35; align = "right"; }
              else if (side === "right") { foX = cx + r + 12; foY = cy - 35; align = "left"; }
              else { foX = cx - 125; foY = cy + r + 45; align = "center"; }

              return (
                <g key={n.id}>
                  {/* Decorative ring hugging every node -- a quiet
                      color-coded dash for done/pending/failed, and a
                      brighter glowing neon ring (blurred + spinning dash)
                      on whichever node is the current active step, so it
                      reads as "this one is live right now". */}
                  {active ? (
                    <>
                      <circle cx={cx} cy={cy} r={r + 10} fill="none" stroke="#2f6fed" strokeWidth="10" opacity=".35" filter="url(#teo-softBlur)" className="teo-node-neon-blur" />
                      <circle cx={cx} cy={cy} r={r + 9} fill="none" stroke="#2f6fed" strokeWidth="2.5" strokeDasharray="8 6" opacity=".85" className="teo-spin-slow" />
                    </>
                  ) : (
                    <>
                      <circle cx={cx} cy={cy} r={r + 8} fill="none" stroke={done ? "#3fbf6f" : failed ? "#e05757" : "#9b7bf0"} strokeWidth="1.8" strokeDasharray="4 6" opacity=".7" />
                      <circle cx={cx} cy={cy} r={r + 18} fill="none" stroke={done ? "#3fbf6f" : failed ? "#e05757" : "#9b7bf0"} strokeWidth="1.4" strokeDasharray="3 8" opacity=".4" />
                    </>
                  )}

                  {showSquare ? (
                    <>
                      <circle cx={cx} cy={cy} r={r} fill="#eaf1ff" />
                      <rect x={cx - 40} y={cy - 40} width="80" height="80" rx="20" fill="url(#teo-talSq)" />
                      <g transform={`translate(${cx - 20},${cy - 20})`}>
                        <NodeIcon type="cube" />
                      </g>
                    </>
                  ) : (
                    <>
                      <circle cx={cx} cy={cy} r={r} fill={done ? "#e6f7ec" : failed ? "#fdeaea" : "#ffffff"} stroke={done || failed ? "none" : "#eef1f9"} strokeWidth={done || failed ? 0 : 1.5} />
                      <g transform={`translate(${cx - 20},${cy - 20})`} color={done ? "#2f6fed" : failed ? "#c62828" : "#3a4560"}>
                        <NodeIcon type={failed && square ? "plug" : slot.icon} />
                      </g>
                      {done && (
                        <>
                          <circle cx={cx + r * 0.55} cy={cy + r * 0.55} r="11" fill="#16a34a" stroke="#fff" strokeWidth="2" />
                          <path d={`M ${cx + r * 0.55 - 5},${cy + r * 0.55} l 3,3 l 6,-7`} stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                        </>
                      )}
                    </>
                  )}

                  <foreignObject x={foX} y={foY} width={foW} height={foH}>
                    <div xmlns="http://www.w3.org/1999/xhtml" className={`teo-${align}`}>
                      <div className={`teo-n-title ${failed ? "teo-n-title-red" : ""}`}>{n.label}</div>
                      <div className={`teo-n-sub teo-${subClass}`}>{n.caption}</div>
                      {active && (
                        <div className="teo-valdots"><span></span><span></span><span></span></div>
                      )}
                    </div>
                  </foreignObject>
                </g>
              );
            })}

            {/* ENGINE CENTER */}
            <circle cx="755" cy="420" r="230" fill={`url(#teo-glowGrad-${theme})`} className="teo-glowc" filter="url(#teo-softBlur)" />

            {/* Crisp white halo ring sitting between the outer blue glow and
                the cube itself -- the reference icon reads as three distinct
                rings (blue glow / solid white / cube), not one soft blur, so
                this is a flat, mostly-opaque white disc rather than another
                gradient. A soft blurred "neon tube" stroke sits right on its
                edge so the ring itself looks lit, not just a flat cut-out. */}
            <circle cx="755" cy="420" r="140" fill="none" stroke="#ffffff" strokeWidth="20" opacity=".7" filter="url(#teo-softBlur)" className="teo-ring-neon" />
            <circle cx="755" cy="420" r="140" fill="#ffffff" opacity=".92" />
            <circle cx="755" cy="420" r="130" fill={`url(#teo-glowCore-${theme})`} className="teo-glowc" />

            {/* Tight decorative "tech ring" hugging the glow -- small chevron
                ticks (not plain dashes) pointing the direction of travel,
                so it reads as an arrowed scanning collar around the cube
                like the reference icon. */}
            <g className="teo-spin-slow">
              {CHEVRON_ANGLES.map(deg => {
                const rad = (deg * Math.PI) / 180;
                const x = 755 + 152 * Math.cos(rad);
                const y = 420 + 152 * Math.sin(rad);
                return (
                  <path
                    key={deg}
                    d="M -5,-4 L 4,0 L -5,4"
                    stroke={complete ? "#5fc98a" : "#5b98ee"}
                    strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" fill="none" opacity=".85"
                    transform={`translate(${x},${y}) rotate(${deg + 90})`}
                  />
                );
              })}
            </g>

            <circle cx="392" cy="152" r="4" fill="#3fbf6f" opacity=".8" />
            <circle cx="512" cy="163" r="2.5" fill="#f0b429" opacity=".8" />
            <circle cx="1000" cy="490" r="3" fill="#c9d6ee" opacity=".7" />
            <path d="M625 300 l5 12 l12 5 l-12 5 l-5 12 l-5 -12 l-12 -5 l12 -5 z" fill="#ffffff" opacity=".85" />

            {/* Ambient blurred glow behind the base -- the reference cube
                photo already includes its own two-step pedestal, this is
                only the light it casts underneath. */}
            <ellipse cx="755" cy="540" rx="104" ry="22" fill={complete ? "#3fbf6f" : "#5b98ee"} opacity=".2" filter="url(#teo-softBlur)" className="teo-pedestal-glow" />

            {/* Soft glow hugging the cube's own silhouette -- separate from
                the big background glow/white ring, this is what makes the
                cube itself look lit rather than a flat shape sitting on a
                plain white disc. */}
            <circle cx="755" cy="410" r="92" fill="#ffffff" opacity=".55" filter="url(#teo-softBlur)" className="teo-glowc" />

            {/* The actual reference cube image, not a redrawn approximation
                -- clipped to a circle (relative to its own bounding box) so
                the screenshot's rectangular edges never show past the glow
                rings around it. */}
            <image
              href={tallyCubeImg}
              x="670" y="310" width="170" height="236"
              preserveAspectRatio="xMidYMid slice"
              clipPath="url(#teo-cube-photo-clip)"
              className="teo-cubegrp"
            />

            <foreignObject x="635" y="565" width="240" height="40">
              <div xmlns="http://www.w3.org/1999/xhtml" className="teo-center" style={{ fontWeight: 700, fontSize: 19, color: "#1a2340" }}>{coreLabel}</div>
            </foreignObject>

          </svg>
        </div>
  );

  if (bare) {
    return <div className={`teo-stage teo-theme-${theme} ${className}`}>{orbitSvg}</div>;
  }

  return (
    <div className={`teo-stage teo-theme-${theme} ${className}`}>
      <div className="teo-panel">

        <div className="teo-status-row">
          <span className="teo-dot-pulse"></span>
          <span>{heading.toUpperCase()}</span>
        </div>
        <div className="teo-subtitle">{subheading}</div>

        {orbitSvg}

        <div className="teo-bottom-bar">
          <div className="teo-op-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8">
              <path d="M4 21V9l8-5 8 5v12" />
              <path d="M9 21v-6h6v6" />
              <path d="M9 12h.01M12 12h.01M15 12h.01M9 9h.01M12 9h.01M15 9h.01" />
            </svg>
          </div>
          <div className="teo-op-text">
            <div className="teo-op-title">CURRENT OPERATION</div>
            <div className="teo-op-name">{operation.title}</div>
            <div className="teo-op-desc">{operation.detail}</div>
          </div>
          <div className="teo-meta">
            <div className="teo-col">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 3" /></svg>
              <div><div className="teo-m-title">Estimated time remaining</div><div className="teo-m-sub">{eta}</div></div>
            </div>
            <div className="teo-col">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
              <div><div className="teo-m-title">Data is secure</div><div className="teo-m-sub">{security}</div></div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
