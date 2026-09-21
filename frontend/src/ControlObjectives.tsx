import React, { useEffect, useRef, useState } from "react";
import Layout from "./Layout";
import type {
  AssessmentPlan,
  DimensionAssessment,
  DimensionStatus,
  HistoryEntry,
  ProposedItem,
} from "./plans";
import {
  clearActiveRecommendation,
  clearHistory as clearHistoryStore,
  getCurrentCard,
  getCurrentPlan,
  getHistory,
  pushActiveRecommendation,
  recordRun,
  setArchived as setArchivedStore,
  setCurrentCard as setCurrentCardStore,
  setCurrentPlan,
} from "./plans";

// Control Objectives service base URL — its own process/port (SPEC §0), overridable per env.
const CONTROL_OBJECTIVES_URL =
  (import.meta as any).env?.VITE_CONTROL_OBJECTIVES_URL || "http://localhost:8090";

// The catalogue is a separate app/origin; back-links point there by URL.
const CATALOGUE_URL =
  (import.meta as any).env?.VITE_CATALOGUE_URL ||
  "http://localhost:3000/catalogue";

// Types (AssessmentPlan, ProposedItem, DimensionAssessment, HistoryEntry) and
// the persistence/recommendation helpers live in services/plans.ts, shared
// with the catalogue's "Recommended" filter.

// ─── Styles ──────────────────────────────────────────────────────────────────

const s = {
  main: {
    flex: 1,
    padding: "40px 24px 60px 24px",
    overflowY: "auto" as const,
    fontFamily: "Arial, sans-serif",
    display: "flex",
    justifyContent: "center",
  },
  inner: { width: "100%", maxWidth: "820px" },
  back: {
    background: "none",
    border: "none",
    color: "#000FDF",
    fontSize: "13px",
    fontWeight: 600 as const,
    cursor: "pointer",
    padding: 0,
    marginBottom: "20px",
  },
  badge: {
    display: "inline-block",
    padding: "4px 12px",
    borderRadius: "999px",
    backgroundColor: "#EEF1FF",
    color: "#000FDF",
    fontSize: "12px",
    fontWeight: 700 as const,
    letterSpacing: "0.4px",
    marginBottom: "16px",
  },
  title: {
    fontSize: "30px",
    fontWeight: 800 as const,
    color: "#1a1a2e",
    margin: "0 0 12px 0",
    lineHeight: 1.2,
  },
  lead: {
    fontSize: "16px",
    lineHeight: 1.6,
    color: "#49454F",
    margin: "0 0 28px 0",
  },
  steps: { display: "grid", gap: "14px", marginBottom: "32px" },
  step: {
    display: "flex",
    gap: "14px",
    alignItems: "flex-start",
    backgroundColor: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: "12px",
    padding: "16px 18px",
  },
  stepNum: {
    flexShrink: 0,
    width: "28px",
    height: "28px",
    borderRadius: "50%",
    backgroundColor: "#000FDF",
    color: "#fff",
    fontSize: "14px",
    fontWeight: 700 as const,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  stepTitle: {
    fontSize: "15px",
    fontWeight: 700 as const,
    color: "#1a1a2e",
    margin: "0 0 3px 0",
  },
  stepText: { fontSize: "13.5px", lineHeight: 1.5, color: "#6c757d", margin: 0 },
  uploadCard: {
    backgroundColor: "#F6F8FF",
    border: "2px dashed #C5D2FF",
    borderRadius: "14px",
    padding: "32px 24px",
    textAlign: "center" as const,
  },
  uploadTitle: {
    fontSize: "17px",
    fontWeight: 700 as const,
    color: "#1a1a2e",
    margin: "0 0 6px 0",
  },
  uploadText: {
    fontSize: "13.5px",
    color: "#6c757d",
    margin: "0 0 18px 0",
    lineHeight: 1.5,
  },
  btnRow: { display: "flex", gap: "12px", justifyContent: "center", flexWrap: "wrap" as const },
  uploadBtn: (disabled: boolean) => ({
    display: "inline-block",
    padding: "11px 22px",
    borderRadius: "8px",
    backgroundColor: disabled ? "#C5D2FF" : "#000FDF",
    color: disabled ? "#5a6477" : "#fff",
    fontSize: "14px",
    fontWeight: 700 as const,
    border: "none",
    cursor: disabled ? "default" : "pointer",
  }),
  note: { marginTop: "14px", fontSize: "12px", color: "#9aa0ad" },
  error: {
    marginTop: "18px",
    padding: "12px 14px",
    borderRadius: "8px",
    backgroundColor: "#fff1f0",
    border: "1px solid #ffccc7",
    color: "#a8071a",
    fontSize: "13px",
    textAlign: "left" as const,
  },
  btnLoading: { display: "inline-flex", alignItems: "center", gap: "8px" },
  loadingPanel: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    gap: "10px",
    textAlign: "center" as const,
    padding: "40px 24px",
    marginTop: "24px",
    backgroundColor: "#F6F8FF",
    border: "1px solid #dbe2ff",
    borderRadius: "14px",
  },
  loadingTitle: { fontSize: "16px", fontWeight: 700 as const, color: "#1a1a2e" },
  loadingText: { fontSize: "13px", color: "#6c757d", maxWidth: "420px", lineHeight: 1.5 },
  // ── results ──
  resultHead: {
    fontSize: "22px",
    fontWeight: 800 as const,
    color: "#1a1a2e",
    margin: "36px 0 4px 0",
  },
  resultSub: { fontSize: "13px", color: "#6c757d", margin: "0 0 20px 0" },
  groupTitle: {
    fontSize: "15px",
    fontWeight: 700 as const,
    color: "#1a1a2e",
    margin: "22px 0 10px 0",
  },
  item: {
    backgroundColor: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: "10px",
    padding: "14px 16px",
    marginBottom: "10px",
  },
  itemHead: { display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" },
  itemName: { fontSize: "14px", fontWeight: 700 as const, color: "#1a1a2e" },
  scoreBadge: (score: number) => ({
    display: "inline-block",
    padding: "2px 9px",
    borderRadius: "999px",
    fontSize: "11px",
    fontWeight: 700 as const,
    whiteSpace: "nowrap" as const,
    // stronger recommendation → bolder blue
    backgroundColor: score >= 4 ? "#000FDF" : score >= 2 ? "#C5D2FF" : "#eef0f3",
    color: score >= 4 ? "#fff" : score >= 2 ? "#1a2e8a" : "#6c757d",
  }),
  rationale: { fontSize: "13px", color: "#49454F", lineHeight: 1.5, margin: "0 0 6px 0" },
  covers: { fontSize: "11.5px", color: "#6c757d" },
  banner: (kind: "gap" | "warn") => ({
    padding: "12px 14px",
    borderRadius: "8px",
    marginTop: "16px",
    fontSize: "13px",
    lineHeight: 1.5,
    backgroundColor: kind === "gap" ? "#fffbe6" : "#f6f8ff",
    border: `1px solid ${kind === "gap" ? "#ffe58f" : "#dbe2ff"}`,
    color: "#49454F",
  }),
  // ── dimension-first layout ──
  resultTop: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
    gap: "16px",
    flexWrap: "wrap" as const,
    margin: "36px 0 4px 0",
  },
  downloadBtn: {
    display: "inline-block",
    padding: "9px 18px",
    borderRadius: "8px",
    backgroundColor: "#000FDF",
    color: "#fff",
    fontSize: "13px",
    fontWeight: 700 as const,
    textDecoration: "none",
    border: "none",
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
  },
  dimCard: {
    backgroundColor: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: "12px",
    padding: "18px 20px",
    marginBottom: "16px",
  },
  dimHead: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "10px",
    cursor: "pointer",
    userSelect: "none" as const,
  },
  dimHeadLeft: { display: "flex", alignItems: "center", gap: "8px" },
  caret: (open: boolean) => ({
    display: "inline-block",
    fontSize: "12px",
    color: "#6c757d",
    transition: "transform 0.15s ease",
    transform: open ? "rotate(90deg)" : "rotate(0deg)",
  }),
  dimBody: { marginTop: "8px" },
  dimTitle: { fontSize: "16px", fontWeight: 800 as const, color: "#1a1a2e", margin: 0 },
  statusPill: (st: DimensionStatus) => ({
    display: "inline-block",
    padding: "3px 11px",
    borderRadius: "999px",
    fontSize: "11px",
    fontWeight: 700 as const,
    textTransform: "uppercase" as const,
    letterSpacing: "0.4px",
    color: "#fff",
    backgroundColor:
      st === "covered" ? "#2f855a" : st === "partial" ? "#b7791f" : "#c53030",
  }),
  dimRelevance: { fontSize: "13px", fontStyle: "italic" as const, color: "#6c757d", margin: "0 0 10px 0" },
  blockLabel: {
    fontSize: "12px",
    fontWeight: 700 as const,
    color: "#000FDF",
    margin: "10px 0 4px 0",
  },
  bullets: { margin: "0 0 4px 0", paddingLeft: "18px", fontSize: "13px", color: "#49454F", lineHeight: 1.5 },
  emptyCovered: { fontSize: "13px", color: "#2f855a", margin: "6px 0 0 0" },
  // ── run history ──
  historyPanel: {
    backgroundColor: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: "12px",
    padding: "14px 16px",
    marginTop: "24px",
  },
  historyHead: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "8px",
  },
  historyTitle: { fontSize: "14px", fontWeight: 700 as const, color: "#1a1a2e" },
  clearBtn: {
    background: "none",
    border: "none",
    color: "#c53030",
    fontSize: "12px",
    fontWeight: 700 as const,
    cursor: "pointer",
    padding: 0,
  },
  historyRow: (active: boolean) => ({
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "10px",
    padding: "8px 10px",
    borderRadius: "8px",
    cursor: "pointer",
    border: `1px solid ${active ? "#000FDF" : "#eef0f3"}`,
    backgroundColor: active ? "#F6F8FF" : "#fff",
    marginBottom: "6px",
  }),
  gapList: { margin: "6px 0 0 0", paddingLeft: "18px", lineHeight: 1.5 },
  diagnostics: {
    marginTop: "16px",
    fontSize: "12px",
    color: "#9aa0ad",
    border: "1px solid #eef0f3",
    borderRadius: "8px",
    padding: "8px 12px",
  },
  diagnosticsSummary: {
    cursor: "pointer",
    fontWeight: 600 as const,
    color: "#6c757d",
    userSelect: "none" as const,
  },
  diagnosticsList: {
    margin: "8px 0 0 0",
    paddingLeft: "18px",
    lineHeight: 1.5,
    fontFamily: "monospace",
    fontSize: "11px",
    color: "#9aa0ad",
    maxHeight: "220px",
    overflowY: "auto" as const,
  },
  historyName: {
    fontSize: "13px",
    fontWeight: 600 as const,
    color: "#1a1a2e",
    display: "flex",
    alignItems: "center",
    gap: "7px",
  },
  historyMeta: { fontSize: "11.5px", color: "#6c757d", display: "flex", alignItems: "center", gap: "8px" },
  historyHint: { fontSize: "12px", color: "#6c757d", margin: "0 0 10px 0" },
  rowAction: {
    background: "none",
    border: "1px solid #d6dae2",
    borderRadius: "6px",
    color: "#6c757d",
    fontSize: "11px",
    fontWeight: 700 as const,
    cursor: "pointer",
    padding: "2px 8px",
  },
  archivedBox: {
    marginTop: "6px",
    marginBottom: "4px",
    border: "1px solid #eef0f3",
    borderRadius: "8px",
    padding: "8px 10px",
    backgroundColor: "#fafbfc",
  },
  archivedSummary: {
    cursor: "pointer",
    fontSize: "12px",
    fontWeight: 700 as const,
    color: "#6c757d",
    userSelect: "none" as const,
    marginBottom: "8px",
  },
  activeDot: {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    backgroundColor: "#000FDF",
    display: "inline-block",
  },
  activeTag: { color: "#000FDF", fontWeight: 700 as const, fontSize: "11px" },
  catalogueLink: {
    display: "inline-block",
    marginTop: "8px",
    fontSize: "13px",
    fontWeight: 700 as const,
    color: "#000FDF",
    textDecoration: "none",
  },
  // ── uploaded system card ──
  cardPanel: {
    backgroundColor: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: "12px",
    padding: "14px 18px",
    marginTop: "24px",
  },
  cardSummary: {
    cursor: "pointer",
    userSelect: "none" as const,
    fontSize: "14px",
    fontWeight: 700 as const,
    color: "#1a1a2e",
    display: "flex",
    alignItems: "center",
    gap: "8px",
  },
  cardMeta: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    gap: "10px 18px",
    margin: "14px 0",
  },
  cardField: { fontSize: "12.5px", color: "#49454F", lineHeight: 1.5 },
  cardFieldLabel: {
    display: "block",
    fontSize: "11px",
    fontWeight: 700 as const,
    textTransform: "uppercase" as const,
    letterSpacing: "0.4px",
    color: "#9aa0ad",
    marginBottom: "2px",
  },
  cardDesc: { fontSize: "13px", color: "#49454F", lineHeight: 1.55, margin: "10px 0" },
  cardRaw: {
    marginTop: "10px",
    background: "#0d1117",
    color: "#c9d1d9",
    borderRadius: "8px",
    padding: "12px 14px",
    fontFamily: "monospace",
    fontSize: "11.5px",
    lineHeight: 1.5,
    maxHeight: "360px",
    overflow: "auto" as const,
    whiteSpace: "pre" as const,
  },
  cardRawSummary: {
    cursor: "pointer",
    userSelect: "none" as const,
    fontSize: "12px",
    fontWeight: 700 as const,
    color: "#6c757d",
  },
};

// Pull a display string from an unknown card field (string, number, or list).
const cardStr = (v: unknown): string | null => {
  if (v == null) return null;
  if (typeof v === "string") return v.trim() || null;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) {
    const parts = v.map(cardStr).filter(Boolean);
    return parts.length ? parts.join(", ") : null;
  }
  return null;
};

// The uploaded system card: a few well-known fields when present, plus the full
// raw JSON. Robust to arbitrary card shapes (users upload their own).
const SystemCardPanel: React.FC<{ card: any }> = ({ card }) => {
  const [open, setOpen] = useState(true);
  const name = cardStr(card?.system_name) || cardStr(card?.name) || "System card";
  const allFields: Array<[string, string | null]> = [
    ["Provider", cardStr(card?.provider)],
    ["Version", cardStr(card?.system_version) || cardStr(card?.version)],
    ["Classification", cardStr(card?.classification)],
    ["Use case", cardStr(card?.target_use_case) || cardStr(card?.use_case)],
    ["Target users", cardStr(card?.target_users)],
    ["Qualification id", cardStr(card?.qualification_id)],
  ];
  const fields = allFields.filter(([, v]) => Boolean(v));
  const description =
    cardStr(card?.description) || cardStr(card?.overview) || null;
  const toggle = () => setOpen((o) => !o);

  // Controlled collapsible (mirrors DimensionSection) — a styled <summary> with
  // display:flex drops the native disclosure marker, so we render our own caret.
  return (
    <div style={s.cardPanel}>
      <div
        style={s.cardSummary}
        onClick={toggle}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <span style={s.caret(open)} aria-hidden="true">▸</span>
        <span>📄 System card — {name}</span>
      </div>

      {open && (
        <div style={s.dimBody}>
          {fields.length > 0 && (
            <div style={s.cardMeta}>
              {fields.map(([label, value]) => (
                <div key={label} style={s.cardField}>
                  <span style={s.cardFieldLabel}>{label}</span>
                  {value}
                </div>
              ))}
            </div>
          )}

          {description && <p style={s.cardDesc}>{description}</p>}

          <details style={{ marginTop: "6px" }}>
            <summary style={s.cardRawSummary}>Raw JSON</summary>
            <pre style={s.cardRaw}>{JSON.stringify(card, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
};

const STEPS = [
  {
    title: "Share your system card",
    text: "Upload the system card describing your AI solution — what it does, the sector it serves, and the people it affects.",
  },
  {
    title: "The service reads it",
    text: "It understands your solution and matches it against everything in the catalogue.",
  },
  {
    title: "Get a tailored shortlist",
    text: "You receive a recommended set of tests and control checklists appropriate for your specific system, each with a reason — no more guessing which apply.",
  },
];

// ─── Component ───────────────────────────────────────────────────────────────

// Self-contained spinner (SVG SMIL rotation — no global @keyframes needed).
const Spinner: React.FC<{ size?: number; color?: string }> = ({
  size = 16,
  color = "#fff",
}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" style={{ display: "block" }}>
    <circle cx="12" cy="12" r="9" fill="none" stroke={color} strokeOpacity="0.25" strokeWidth="3" />
    <path d="M12 3a9 9 0 0 1 9 9" fill="none" stroke={color} strokeWidth="3" strokeLinecap="round">
      <animateTransform
        attributeName="transform"
        type="rotate"
        from="0 12 12"
        to="360 12 12"
        dur="0.8s"
        repeatCount="indefinite"
      />
    </path>
  </svg>
);

const ItemList: React.FC<{ items: ProposedItem[] }> = ({ items }) => (
  <>
    {[...items]
      .sort((a, b) => b.score - a.score)
      .map((it) => (
      <div key={it.item_id} style={s.item}>
        <div style={s.itemHead}>
          <span style={s.itemName}>{it.item_id}</span>
          <span style={s.scoreBadge(it.score)}>★ {it.score}/5</span>
          <span style={s.covers}>({it.item_type})</span>
        </div>
        <p style={s.rationale}>{it.rationale}</p>
        {it.covers.length > 0 && (
          <div style={s.covers}>Covers: {it.covers.join(", ")}</div>
        )}
      </div>
    ))}
  </>
);

// One trustworthiness dimension: status, what the card already covers, the
// residual gaps, and the items recommended to close them (mirror of the PDF).
const DimensionSection: React.FC<{
  dim: DimensionAssessment;
  itemsById: Record<string, ProposedItem>;
  defaultOpen?: boolean;
}> = ({ dim, itemsById, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  const items = dim.recommended_item_ids
    .map((id) => itemsById[id])
    .filter((it): it is ProposedItem => Boolean(it));
  const toggle = () => setOpen((o) => !o);
  return (
    <div style={s.dimCard}>
      <div
        style={s.dimHead}
        onClick={toggle}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <span style={s.dimHeadLeft}>
          <span style={s.caret(open)} aria-hidden="true">▸</span>
          <h3 style={s.dimTitle}>{dim.label}</h3>
        </span>
        <span style={s.statusPill(dim.status)}>{dim.status}</span>
      </div>

      {open && (
        <div style={s.dimBody}>
          {dim.relevance_reason && <p style={s.dimRelevance}>{dim.relevance_reason}</p>}

          {dim.already_addressed.length > 0 && (
            <>
              <div style={s.blockLabel}>Already addressed by the system</div>
              <ul style={s.bullets}>
                {dim.already_addressed.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </>
          )}

          {dim.residual_gaps.length > 0 && (
            <>
              <div style={s.blockLabel}>Residual gaps</div>
              <ul style={s.bullets}>
                {dim.residual_gaps.map((g, i) => (
                  <li key={i}>{g}</li>
                ))}
              </ul>
            </>
          )}

          {items.length > 0 ? (
            <>
              <div style={s.blockLabel}>Recommended</div>
              <ItemList items={items} />
            </>
          ) : (
            dim.residual_gaps.length === 0 && (
              <p style={s.emptyCovered}>
                Already adequately covered — no additional items required.
              </p>
            )
          )}
        </div>
      )}
    </div>
  );
};

const ControlObjectives: React.FC = () => {
  const fileRef = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<AssessmentPlan | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [card, setCard] = useState<any | null>(null);

  // restore the selected result + its card + history once, on mount
  useEffect(() => {
    setPlan(getCurrentPlan());
    setCard(getCurrentCard());
    setHistory(getHistory());
  }, []);

  // Select a run as the active plan: shown here AND used by the catalogue's
  // "Recommended" filter. Persisted locally (survives refresh) AND pushed to the
  // Control Objectives API, which is the source of truth the catalogue reads (cross-origin).
  const selectPlan = (p: AssessmentPlan | null) => {
    setPlan(p);
    setCurrentPlan(p);
    if (p) {
      pushActiveRecommendation(p).catch(() => {
        /* non-fatal: the catalogue just won't show stars until the next run */
      });
    } else {
      clearActiveRecommendation().catch(() => {});
    }
  };

  // Show a run together with the system card it was produced from (state +
  // persistence stay in lock-step).
  const showRun = (p: AssessmentPlan | null, c: unknown = null) => {
    selectPlan(p);
    setCard(c ?? null);
    setCurrentCardStore(c ?? null);
  };

  const clearHistory = () => {
    setHistory([]);
    clearHistoryStore();
  };

  // Archive a run (tuck it into the Archived section). If it's the active plan,
  // also clear the selection so an archived run stops driving Recommended.
  const archiveRun = (planId: string) => {
    setHistory(setArchivedStore(planId, true));
    if (plan?.plan_id === planId) showRun(null);
  };

  const restoreRun = (planId: string) => {
    setHistory(setArchivedStore(planId, false));
  };

  // Render the PDF from the plan we hold locally: the service keeps plans only in
  // memory, so the GET-by-id route 404s after a restart. POST the full plan to
  // the stateless render endpoint and trigger a blob download — works for any
  // run, including old ones restored from localStorage.
  const downloadPdf = async () => {
    if (!plan || pdfBusy) return;
    setPdfBusy(true);
    setError(null);
    try {
      const res = await fetch(`${CONTROL_OBJECTIVES_URL}/api/plans/render-pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(plan),
      });
      if (!res.ok) throw new Error(`Control Objectives returned ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `assessment-plan-${plan.plan_id}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(
        e?.message?.includes("Failed to fetch")
          ? `Could not reach the Control Objectives service at ${CONTROL_OBJECTIVES_URL}. Is it running?`
          : `Couldn't generate the PDF (${e?.message || "unknown error"}).`
      );
    } finally {
      setPdfBusy(false);
    }
  };

  const runAssessment = async (card: unknown) => {
    setLoading(true);
    setError(null);
    // show the uploaded card immediately — during analysis and even if the run
    // fails — not just once a plan comes back
    setCard(card);
    setCurrentCardStore(card);
    // keep the previous result visible while the new run is in flight
    try {
      const res = await fetch(`${CONTROL_OBJECTIVES_URL}/api/plans/from-card`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card }),
      });
      const data = await res.json();
      if (!res.ok) {
        // failed plans come back 502 with the plan body (warnings explain why)
        const why =
          data?.warnings?.join("; ") ||
          data?.detail ||
          `Control Objectives returned ${res.status}`;
        throw new Error(why);
      }
      const newPlan = data as AssessmentPlan;
      // persists plan + card as current (survives refresh, drives Recommended)
      showRun(newPlan, card);
      setHistory(recordRun(newPlan, card)); // append to the persisted history
    } catch (e: any) {
      setError(
        e?.message?.includes("Failed to fetch")
          ? `Could not reach the Control Objectives service at ${CONTROL_OBJECTIVES_URL}. Is it running?`
          : e?.message || "Something went wrong."
      );
    } finally {
      setLoading(false);
    }
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const card = JSON.parse(await file.text());
      await runAssessment(card);
    } catch {
      setError("That file isn't valid JSON. Please upload a system card JSON file.");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <Layout>
      <div style={s.main}>
        <div style={s.inner}>
          <a style={s.back} href={CATALOGUE_URL}>
            ← Back to catalogue
          </a>

          <span style={s.badge}>RECOMMENDED FOR YOU</span>
          <h1 style={s.title}>Let the service pick the right tests &amp; controls</h1>
          <p style={s.lead}>
            The catalogue holds many tests and control checklists — but not all
            of them apply to your solution. The service does the matching for
            you: tell it about your AI system and it recommends exactly the
            tests and controls you should run.
          </p>

          <div style={s.steps}>
            {STEPS.map((step, i) => (
              <div key={i} style={s.step}>
                <span style={s.stepNum}>{i + 1}</span>
                <div>
                  <p style={s.stepTitle}>{step.title}</p>
                  <p style={s.stepText}>{step.text}</p>
                </div>
              </div>
            ))}
          </div>

          <div style={s.uploadCard}>
            <p style={s.uploadTitle}>Upload your system card</p>
            <p style={s.uploadText}>
              Drop your system card here to get your personalised
              recommendations.
            </p>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              style={{ display: "none" }}
              onChange={onFile}
            />
            <div style={s.btnRow}>
              <button
                style={s.uploadBtn(loading)}
                disabled={loading}
                onClick={() => fileRef.current?.click()}
              >
                {/* No spinner here — the loading panel below is the single
                    progress indicator (avoids two spinners at once). */}
                {loading ? "Analysing…" : "Choose a system card"}
              </button>
            </div>
            <span style={s.note}>
              Your system card stays between your browser and the Control Objectives service.
            </span>
            {error && <div style={s.error}>{error}</div>}
          </div>

          {loading && (
            <div style={s.loadingPanel}>
              <Spinner size={34} color="#000FDF" />
              <div style={s.loadingTitle}>Analysing your system card…</div>
              <div style={s.loadingText}>
                The service is reading the card and consulting the catalogue — this
                usually takes up to a minute or two.
              </div>
            </div>
          )}

          {history.length > 0 &&
            (() => {
              const activeRuns = history.filter((h) => !h.archived);
              const archivedRuns = history.filter((h) => h.archived);
              return (
                <div style={s.historyPanel}>
                  <div style={s.historyHead}>
                    <span style={s.historyTitle}>
                      Previous runs ({activeRuns.length})
                    </span>
                    <button style={s.clearBtn} onClick={clearHistory}>
                      Clear history
                    </button>
                  </div>
                  <p style={s.historyHint}>
                    The selected run drives the catalogue’s{" "}
                    <strong>Recommended</strong> filter. Click a run to make it
                    active.
                  </p>
                  {activeRuns.map((h) => {
                    const active = plan?.plan_id === h.plan_id;
                    return (
                      <div
                        key={h.plan_id}
                        style={s.historyRow(active)}
                        onClick={() => showRun(h.plan, h.card)}
                        title={active ? "Active for Recommended" : "Select this run"}
                      >
                        <span style={s.historyName}>
                          {active && (
                            <span style={s.activeDot} aria-hidden="true" />
                          )}
                          {h.system_name}
                        </span>
                        <span style={s.historyMeta}>
                          {active && <span style={s.activeTag}>● Active</span>}
                          {h.status} · {new Date(h.saved_at).toLocaleString()}
                          <button
                            style={s.rowAction}
                            title="Archive this run"
                            onClick={(e) => {
                              e.stopPropagation();
                              archiveRun(h.plan_id);
                            }}
                          >
                            Archive
                          </button>
                        </span>
                      </div>
                    );
                  })}
                  {activeRuns.length === 0 && (
                    <p style={s.historyHint}>
                      All runs are archived — restore one below to make it active.
                    </p>
                  )}

                  {archivedRuns.length > 0 && (
                    <details style={s.archivedBox}>
                      <summary style={s.archivedSummary}>
                        Archived ({archivedRuns.length})
                      </summary>
                      {archivedRuns.map((h) => (
                        <div key={h.plan_id} style={s.historyRow(false)}>
                          <span style={s.historyName}>{h.system_name}</span>
                          <span style={s.historyMeta}>
                            {h.status} ·{" "}
                            {new Date(h.saved_at).toLocaleString()}
                            <button
                              style={s.rowAction}
                              title="Restore this run"
                              onClick={(e) => {
                                e.stopPropagation();
                                restoreRun(h.plan_id);
                              }}
                            >
                              Restore
                            </button>
                          </span>
                        </div>
                      ))}
                    </details>
                  )}

                  <a href={CATALOGUE_URL} style={s.catalogueLink}>
                    View recommended items in the catalogue →
                  </a>
                </div>
              );
            })()}

          {card && <SystemCardPanel card={card} />}

          {!loading && plan && (
            <>
              <div style={s.resultTop}>
                <h2 style={{ ...s.resultHead, margin: 0 }}>
                  Recommendations for {plan.system_name}
                </h2>
                <button
                  style={{ ...s.downloadBtn, opacity: pdfBusy ? 0.6 : 1 }}
                  onClick={downloadPdf}
                  disabled={pdfBusy}
                >
                  {pdfBusy ? (
                    <span style={s.btnLoading}>
                      <Spinner size={14} /> Generating…
                    </span>
                  ) : (
                    "↓ Download PDF"
                  )}
                </button>
              </div>
              <p style={s.resultSub}>
                {plan.tests.length} test(s), {plan.datasets.length} dataset(s) and{" "}
                {plan.checklists.length} checklist(s) · reviewed over{" "}
                {plan.review_rounds} round(s)
              </p>

              {plan.gaps.length > 0 && (
                <div style={{ ...s.banner("gap"), marginBottom: "28px" }}>
                  <strong>Coverage gaps</strong>
                  <ul style={s.gapList}>
                    {plan.gaps.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </div>
              )}

              {plan.dimensions && plan.dimensions.length > 0 ? (
                (() => {
                  const itemsById: Record<string, ProposedItem> = {};
                  [...plan.tests, ...plan.datasets, ...plan.checklists].forEach(
                    (it) => {
                      itemsById[it.item_id] = it;
                    }
                  );
                  return plan.dimensions.map((dim) => (
                    <DimensionSection
                      key={dim.dimension_slug}
                      dim={dim}
                      itemsById={itemsById}
                    />
                  ));
                })()
              ) : (
                // fallback for plans produced before the dimension-first model
                <>
                  {plan.tests.length > 0 && (
                    <>
                      <div style={s.groupTitle}>Tests</div>
                      <ItemList items={plan.tests} />
                    </>
                  )}
                  {plan.datasets.length > 0 && (
                    <>
                      <div style={s.groupTitle}>Datasets</div>
                      <ItemList items={plan.datasets} />
                    </>
                  )}
                  {plan.checklists.length > 0 && (
                    <>
                      <div style={s.groupTitle}>Control checklists</div>
                      <ItemList items={plan.checklists} />
                    </>
                  )}
                </>
              )}

              {/* Guard diagnostics are internal QA telemetry (evidence checks,
                  dedup, etc.), not guidance — tuck them away, available on demand. */}
              {plan.warnings.length > 0 && (
                <details style={s.diagnostics}>
                  <summary style={s.diagnosticsSummary}>
                    Processing details ({plan.warnings.length})
                  </summary>
                  <ul style={s.diagnosticsList}>
                    {plan.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </div>
      </div>
    </Layout>
  );
};

export default ControlObjectives;
