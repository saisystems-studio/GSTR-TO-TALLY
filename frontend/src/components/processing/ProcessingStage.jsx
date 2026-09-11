import { useMemo } from "react";
import TallyEngineOrbit from "./TallyEngineOrbit";

/* ------------------------------------------------------------------ *
 * Layout map: backend step key -> node identity + stage geometry.
 * This is presentation data. It lives here, NOT in services/, so a
 * backend rename never drags coordinates along with it.
 * ------------------------------------------------------------------ */
export const STEP_LAYOUT = {
  reading_parties:  { id: "source",  label: "Source Data",    icon: "doc",    x: 29, y: 21,   side: "left",   accent: "blue"   },
  fetching_party:   { id: "party",   label: "Party Details",  icon: "users",  x: 64, y: 20,   side: "right",  accent: "green"  },
  tally_connection: { id: "tally",   label: "Tally System",   icon: "cube",   x: 24, y: 52,   side: "left",   accent: "blue"   },
  verify_company:   { id: "verify",  label: "Company Verify", icon: "shield", x: 65, y: 61.5, side: "bottom", accent: "indigo" },
  prepare_masters:  { id: "masters", label: "Masters Ready",  icon: "layers", x: 84, y: 47,   side: "right",  accent: "violet" },
};

/* Wires are declared against node ids, so they survive a step rename. */
const LINKS = [
  { from: "source",  to: "core",    tone: "blue"   },
  { from: "party",   to: "core",    tone: "blue"   },
  { from: "tally",   to: "core",    tone: "blue"   },
  { from: "verify",  to: "core",    tone: "violet" },
  { from: "party",   to: "masters", tone: "violet", bow: 0.34, solid: true },
  { from: "masters", to: "verify",  tone: "violet", bow: -0.2 },
];

/* Backend vocabulary -> the three visual states the stage understands. */
const STATE_ALIASES = {
  completed: "done",
  complete: "done",
  success: "done",
  warning: "done",              // "Completed with Warning" still reads as finished
  completed_with_warning: "done",
  running: "active",
  in_progress: "active",
  processing: "active",
  queued: "pending",
  idle: "pending",
  failed: "pending",
};

const normalizeState = (raw) => {
  const key = String(raw ?? "").trim().toLowerCase().replace(/\s+/g, "_");
  return STATE_ALIASES[key] || (["done", "active", "pending"].includes(key) ? key : "pending");
};

/**
 * Expects steps shaped like:
 *   { key, state, title, description, detail }
 * `key` must exist in STEP_LAYOUT — anything else is dropped rather than
 * rendered at left:undefined.
 */
export default function ProcessingStage({
  steps = [],
  eta = "Just a few seconds",
  security = "256-bit encrypted transfer",
  className = "",
}) {
  const { nodes, current, allDone } = useMemo(() => {
    const normalized = steps
      .map((s) => ({ ...s, state: normalizeState(s.state) }))
      .filter((s) => STEP_LAYOUT[s.key]);

    // Only the first running step gets the scan ring — two spinners at once
    // reads as two things happening, which is never true here.
    let activeSeen = false;
    const mapped = normalized.map((s) => {
      const layout = STEP_LAYOUT[s.key];
      let state = s.state;
      if (state === "active") {
        if (activeSeen) state = "pending";
        else activeSeen = true;
      }
      return {
        ...layout,
        status: state,
        caption: s.detail || (state === "done" ? "Ready" : state === "active" ? "Working" : "Pending"),
        accent: state === "done" ? "green" : layout.accent,
      };
    });

    const running = normalized.find((s) => s.state === "active");
    const lastDone = [...normalized].reverse().find((s) => s.state === "done");
    const head = running || lastDone;

    return {
      nodes: mapped,
      allDone: normalized.length > 0 && normalized.every((s) => s.state === "done"),
      current: {
        title: head?.title || "Idle",
        detail: head?.description || "No background operation is currently running.",
        icon: "building",
      },
    };
  }, [steps]);

  // Nothing to draw yet — don't render an empty orbit skeleton.
  if (!nodes.length) return null;

  return (
    <TallyEngineOrbit
      className={className}
      nodes={nodes}
      links={LINKS}
      status={allDone ? "complete" : "live"}
      heading={allDone ? "Verification complete" : "Live processing"}
      subheading={
        allDone
          ? "Tally connection, company and license are verified."
          : "Your data is being processed securely in the background."
      }
      operation={current}
      eta={allDone ? "Done" : eta}
      security={security}
    />
  );
}
