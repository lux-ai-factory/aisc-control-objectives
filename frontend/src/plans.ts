// Shared store for Control Objectives assessment plans.
//
// Plans live only in the Control Objectives service's in-memory store (lost on its restart)
// and in React state (lost on refresh). We persist them in the browser so:
//   • the current result survives a refresh,
//   • every run is kept in a history the user can revisit, and
//   • the *currently selected* run drives the catalogue's "Recommended" filter.
//
// Both the service page (writer) and the Catalogue page (reader of the active
// selection) import from here, so the contract lives in one place.

// ─── Types (mirror control objectives.models.plan) ───────────────────────────────────────

export interface ProposedItem {
  item_id: string;
  item_type: "test" | "dataset" | "checklist";
  /** recommendation strength 1 (weak) … 5 (strong). */
  score: number;
  rationale: string;
  evidence: string[];
  covers: string[];
  paired_test_id?: string | null;
  dimension_slugs?: string[];
}

export type DimensionStatus = "covered" | "partial" | "gap";

export interface DimensionAssessment {
  dimension_slug: string;
  label: string;
  in_scope: boolean;
  relevance_reason: string;
  already_addressed: string[];
  residual_gaps: string[];
  recommended_item_ids: string[];
  status: DimensionStatus;
}

export interface AssessmentPlan {
  plan_id: string;
  system_name: string;
  status: string;
  tests: ProposedItem[];
  datasets: ProposedItem[];
  checklists: ProposedItem[];
  coverage: Record<string, string[]>;
  gaps: string[];
  dimensions: DimensionAssessment[];
  warnings: string[];
  review_rounds: number;
}

export interface HistoryEntry {
  plan_id: string;
  system_name: string;
  status: string;
  saved_at: string; // client-side ISO timestamp
  plan: AssessmentPlan;
  /** the raw system card this run was produced from, kept so it can be shown
   * again when the run is revisited. */
  card?: unknown;
  /** archived runs are tucked into a separate section and don't clutter the
   * active list; kept (not deleted) so they can be restored. */
  archived?: boolean;
}

// ─── Storage ──────────────────────────────────────────────────────────────────

// LS_CURRENT holds the *selected* plan — the one whose recommendations the
// catalogue's "Recommended" filter uses. Selecting a different run (or running a
// new card) changes it.
const LS_CURRENT = "control objectives.currentPlan";
const LS_CURRENT_CARD = "control objectives.currentCard";
const LS_HISTORY = "control objectives.history";
const HISTORY_LIMIT = 25;

// Fired on same-tab updates so the catalogue (if mounted) can react without a
// reload. (The native `storage` event only fires across tabs.)
export const CONTROL_OBJECTIVES_PLANS_EVENT = "control objectives-plans-changed";

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function announce(): void {
  try {
    window.dispatchEvent(new Event(CONTROL_OBJECTIVES_PLANS_EVENT));
  } catch {
    /* SSR / no window — ignore */
  }
}

export function getCurrentPlan(): AssessmentPlan | null {
  return loadJSON<AssessmentPlan | null>(LS_CURRENT, null);
}

export function setCurrentPlan(plan: AssessmentPlan | null): void {
  if (plan) localStorage.setItem(LS_CURRENT, JSON.stringify(plan));
  else localStorage.removeItem(LS_CURRENT);
  announce();
}

// The raw system card backing the currently-shown run (so it survives a refresh
// alongside the current plan).
export function getCurrentCard(): unknown | null {
  return loadJSON<unknown | null>(LS_CURRENT_CARD, null);
}

export function setCurrentCard(card: unknown | null): void {
  if (card != null) localStorage.setItem(LS_CURRENT_CARD, JSON.stringify(card));
  else localStorage.removeItem(LS_CURRENT_CARD);
}

export function getHistory(): HistoryEntry[] {
  return loadJSON<HistoryEntry[]>(LS_HISTORY, []);
}

export function recordRun(plan: AssessmentPlan, card?: unknown): HistoryEntry[] {
  const entry: HistoryEntry = {
    plan_id: plan.plan_id,
    system_name: plan.system_name,
    status: plan.status,
    saved_at: new Date().toISOString(),
    plan,
    card,
  };
  const next = [
    entry,
    ...getHistory().filter((h) => h.plan_id !== plan.plan_id),
  ].slice(0, HISTORY_LIMIT);
  localStorage.setItem(LS_HISTORY, JSON.stringify(next));
  announce();
  return next;
}

export function clearHistory(): void {
  localStorage.removeItem(LS_HISTORY);
  announce();
}

// Archive / restore a run. Archived runs stay in storage but are filtered out of
// the active list by the UI; restoring just flips the flag back.
export function setArchived(planId: string, archived: boolean): HistoryEntry[] {
  const next = getHistory().map((h) =>
    h.plan_id === planId ? { ...h, archived } : h
  );
  localStorage.setItem(LS_HISTORY, JSON.stringify(next));
  announce();
  return next;
}

// ─── Recommendations ──────────────────────────────────────────────────────────

// slug → recommendation score (1–5). Plan item_ids ARE catalogue slugs (the
// Control Objectives prefilter keys candidates by slug), so the catalogue matches these
// directly against `tool.slug`. Highest score wins if a slug appears twice.
export function recommendedScores(plan: AssessmentPlan): Record<string, number> {
  const scores: Record<string, number> = {};
  for (const i of [...plan.tests, ...plan.datasets, ...plan.checklists]) {
    scores[i.item_id] = Math.max(scores[i.item_id] ?? 0, i.score);
  }
  return scores;
}

export interface ActiveRecommendation {
  planId: string;
  systemName: string;
  scores: Record<string, number>; // slug → 1–5
  testCount: number; // recommended tests (incl. datasets)
  controlCount: number; // recommended control checklists
}

// The active recommendation = the currently selected plan, reduced to what the
// catalogue needs. null when no plan has been run/selected.
export function getActiveRecommendation(): ActiveRecommendation | null {
  const plan = getCurrentPlan();
  if (!plan) return null;
  return {
    planId: plan.plan_id,
    systemName: plan.system_name,
    scores: recommendedScores(plan),
    testCount: plan.tests.length + plan.datasets.length,
    controlCount: plan.checklists.length,
  };
}

// ─── Active recommendation via the service API (cross-origin source of truth) ──
//
// The Control Objectives service owns "which run drives Recommended" so the catalogue and a
// (possibly separate-origin) Control Objectives UI agree without sharing browser storage.
// The service page pushes/clears the selection; the catalogue reads it.

const CONTROL_OBJECTIVES_URL =
  (import.meta as any).env?.VITE_CONTROL_OBJECTIVES_URL || "http://localhost:8090";

// Make a run the active recommendation server-side. Fire-and-forget from the UI.
export async function pushActiveRecommendation(
  plan: AssessmentPlan
): Promise<void> {
  await fetch(`${CONTROL_OBJECTIVES_URL}/api/recommendation/active`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(plan),
  });
}

export async function clearActiveRecommendation(): Promise<void> {
  await fetch(`${CONTROL_OBJECTIVES_URL}/api/recommendation/active`, { method: "DELETE" });
}

// Read the server-owned active recommendation. null when none is set or the
// service is unreachable (the catalogue then simply shows no recommendation).
export async function fetchActiveRecommendation(): Promise<ActiveRecommendation | null> {
  try {
    const res = await fetch(`${CONTROL_OBJECTIVES_URL}/api/recommendation/active`);
    if (!res.ok) return null;
    const data = await res.json();
    if (!data) return null;
    // API is snake_case; the catalogue's ActiveRecommendation is camelCase.
    return {
      planId: data.plan_id,
      systemName: data.system_name,
      scores: data.scores ?? {},
      testCount: data.test_count ?? 0,
      controlCount: data.control_count ?? 0,
    };
  } catch {
    return null;
  }
}
