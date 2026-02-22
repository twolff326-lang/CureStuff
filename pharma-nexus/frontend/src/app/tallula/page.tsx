"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { fetchApi } from "@/lib/api";
import type {
  CancerType,
  TallulaDiscovery,
  TallulaRunResult,
  TallulaRunSummary,
} from "@/types";

// ---------------------------------------------------------------
// Discovery class colors + labels
// ---------------------------------------------------------------

const CLASS_CONFIG: Record<
  string,
  { color: string; bg: string; border: string; label: string }
> = {
  resonant: {
    color: "text-amber-700",
    bg: "bg-amber-50",
    border: "border-amber-200",
    label: "Resonant",
  },
  robust: {
    color: "text-emerald-700",
    bg: "bg-emerald-50",
    border: "border-emerald-200",
    label: "Robust",
  },
  moderate: {
    color: "text-blue-700",
    bg: "bg-blue-50",
    border: "border-blue-200",
    label: "Moderate",
  },
  fragile: {
    color: "text-red-700",
    bg: "bg-red-50",
    border: "border-red-200",
    label: "Fragile",
  },
  weak: {
    color: "text-slate-500",
    bg: "bg-slate-50",
    border: "border-slate-200",
    label: "Weak",
  },
};

const DIMENSION_LABELS: Record<string, string> = {
  pathway_overlap: "Pathway Overlap",
  expression_correlation: "Expression Correlation",
  literature_support: "Literature Support",
  clinical_evidence: "Clinical Evidence",
  safety: "Safety",
  novelty: "Novelty",
  causal_dependency: "Causal Dependency",
  mutation_context: "Mutation Context",
  polypharmacology: "Polypharmacology",
  pharmacological_response: "Drug Sensitivity",
};

const BAR_COLORS: Record<string, string> = {
  resonant: "#d97706",
  robust: "#059669",
  moderate: "#2563eb",
  fragile: "#dc2626",
  weak: "#94a3b8",
};

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function downloadBlob(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------
// Page
// ---------------------------------------------------------------

export default function TallulaPage() {
  // Config state
  const [cancerTypes, setCancerTypes] = useState<CancerType[]>([]);
  const [cancerTypeId, setCancerTypeId] = useState<number | null>(null);
  const [nLenses, setNLenses] = useState(200);
  const [seed, setSeed] = useState<number | null>(42);

  // Run state
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TallulaRunResult | null>(null);

  // Past runs
  const [pastRuns, setPastRuns] = useState<TallulaRunSummary[]>([]);

  // Filters
  const [classFilter, setClassFilter] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Load cancer types + past runs on mount
  useEffect(() => {
    fetchApi<{ cancer_types: CancerType[] }>("/api/cancer-types?per_page=100")
      .then((d) => setCancerTypes(d.cancer_types))
      .catch(() => {});
    fetchApi<TallulaRunSummary[]>("/api/tallula/runs")
      .then(setPastRuns)
      .catch(() => {});
  }, []);

  // Run the algorithm
  const runTallula = useCallback(async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const body: Record<string, unknown> = {
        n_lenses: nLenses,
        seed,
      };
      if (cancerTypeId) body.cancer_type_id = cancerTypeId;

      const data = await fetchApi<TallulaRunResult>("/api/tallula/run", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setResult(data);
      // Refresh past runs
      fetchApi<TallulaRunSummary[]>("/api/tallula/runs")
        .then(setPastRuns)
        .catch(() => {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run Tallula Algorithm");
    } finally {
      setRunning(false);
    }
  }, [nLenses, seed, cancerTypeId]);

  // Filtered discoveries
  const discoveries = result?.discoveries ?? [];
  const filtered =
    classFilter === "all"
      ? discoveries
      : discoveries.filter((d) => d.discovery_class === classFilter);

  // Summary chart data
  const summaryData = result
    ? ["resonant", "robust", "moderate", "fragile", "weak"]
        .map((cls) => ({
          name: CLASS_CONFIG[cls]?.label ?? cls,
          count: result.summary.class_counts[cls] ?? 0,
          cls,
        }))
        .filter((d) => d.count > 0)
    : [];

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-1">
        Tallula Algorithm
      </h1>
      <p className="text-slate-500 mb-6">
        Stochastic Resonance Ensemble Discovery — surfaces novel drug
        repurposing candidates that deterministic scoring misses.
      </p>

      {/* ── Controls ── */}
      <div className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700 mb-4">
          Run Configuration
        </h2>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Cancer Type
            </label>
            <select
              value={cancerTypeId ?? ""}
              onChange={(e) =>
                setCancerTypeId(e.target.value ? Number(e.target.value) : null)
              }
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 min-w-[200px]"
            >
              <option value="">All cancer types</option>
              {cancerTypes.map((ct) => (
                <option key={ct.id} value={ct.id}>
                  {ct.name} ({ct.tcga_code})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Lenses (K)
            </label>
            <input
              type="number"
              min={10}
              max={2000}
              value={nLenses}
              onChange={(e) => setNLenses(Number(e.target.value))}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 w-24"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Seed
            </label>
            <input
              type="number"
              value={seed ?? ""}
              onChange={(e) =>
                setSeed(e.target.value ? Number(e.target.value) : null)
              }
              placeholder="Random"
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 w-24"
            />
          </div>

          <button
            onClick={runTallula}
            disabled={running}
            className="rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-800 active:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {running && (
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            )}
            {running ? "Running..." : "Run Tallula"}
          </button>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="mb-4 rounded-md bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* ── Running indicator ── */}
      {running && (
        <div className="mb-6 rounded-md bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-700 flex items-center gap-2">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-blue-400/30 border-t-blue-600" />
          Scoring hypotheses across {nLenses} stochastic lenses. This may take
          a moment...
        </div>
      )}

      {/* ── Results ── */}
      {result && (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 mb-6">
            {(["resonant", "robust", "moderate", "fragile", "weak"] as const).map(
              (cls) => {
                const cfg = CLASS_CONFIG[cls];
                const count = result.summary.class_counts[cls] ?? 0;
                return (
                  <button
                    key={cls}
                    onClick={() =>
                      setClassFilter(classFilter === cls ? "all" : cls)
                    }
                    className={`rounded-lg border p-4 text-left transition-all ${
                      classFilter === cls
                        ? `${cfg.border} ${cfg.bg} ring-2 ring-offset-1 ring-current ${cfg.color}`
                        : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <p className="text-2xl font-bold text-slate-900">{count}</p>
                    <p className={`text-xs font-medium ${cfg.color}`}>
                      {cfg.label}
                    </p>
                  </button>
                );
              },
            )}
          </div>

          {/* Summary bar chart */}
          {summaryData.length > 0 && (
            <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-700 mb-3">
                Discovery Classification ({result.parameters.n_hypotheses_input}{" "}
                hypotheses, {result.parameters.n_lenses} lenses)
              </h3>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={summaryData} margin={{ left: 10, right: 10 }}>
                  <XAxis
                    dataKey="name"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                  />
                  <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 8,
                      border: "1px solid #e2e8f0",
                      fontSize: 12,
                    }}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                    {summaryData.map((d) => (
                      <Cell
                        key={d.cls}
                        fill={BAR_COLORS[d.cls] ?? "#94a3b8"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Export buttons */}
          <div className="mb-6 flex flex-wrap gap-2">
            <button
              onClick={() => {
                const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, "");
                downloadBlob(
                  JSON.stringify(result, null, 2),
                  `tallula_run_${result.run_id}_${ts}.json`,
                  "application/json",
                );
              }}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
              </svg>
              Export JSON
            </button>
            <a
              href={`${API_URL}/api/tallula/runs/${result.run_id}/export?format=csv`}
              download
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
              </svg>
              Export CSV
            </a>
          </div>

          {/* Discovery list */}
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-800">
              Discoveries
              {classFilter !== "all" && (
                <span className="ml-2 text-sm font-normal text-slate-500">
                  — showing {CLASS_CONFIG[classFilter]?.label ?? classFilter}
                </span>
              )}
            </h2>
            <span className="text-xs text-slate-400">
              {filtered.length} of {discoveries.length}
            </span>
          </div>

          {filtered.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white px-5 py-12 text-center text-sm text-slate-400">
              No discoveries in this category.
            </div>
          ) : (
            <div className="space-y-2">
              {filtered.map((d) => (
                <DiscoveryCard
                  key={d.hypothesis_id}
                  discovery={d}
                  expanded={expandedId === d.hypothesis_id}
                  onToggle={() =>
                    setExpandedId(
                      expandedId === d.hypothesis_id ? null : d.hypothesis_id,
                    )
                  }
                />
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Past Runs ── */}
      {!result && pastRuns.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700 mb-3">
            Past Runs
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs text-slate-500">
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Hypotheses</th>
                  <th className="pb-2 pr-4">Lenses</th>
                  <th className="pb-2 pr-4">Resonant</th>
                  <th className="pb-2 pr-4">Robust</th>
                  <th className="pb-2 pr-4">Fragile</th>
                </tr>
              </thead>
              <tbody>
                {pastRuns.map((r) => (
                  <tr
                    key={r.id}
                    className="border-b border-slate-50 last:border-0"
                  >
                    <td className="py-2 pr-4 text-slate-600">
                      {r.created_at
                        ? new Date(r.created_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td className="py-2 pr-4">{r.n_hypotheses_input}</td>
                    <td className="py-2 pr-4">{r.n_lenses}</td>
                    <td className="py-2 pr-4 font-medium text-amber-600">
                      {r.n_resonant}
                    </td>
                    <td className="py-2 pr-4 text-emerald-600">
                      {r.n_robust}
                    </td>
                    <td className="py-2 pr-4 text-red-600">{r.n_fragile}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Empty state ── */}
      {!result && pastRuns.length === 0 && !running && (
        <div className="rounded-lg border border-slate-200 bg-white px-5 py-16 text-center shadow-sm">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100">
            <svg
              className="h-6 w-6 text-amber-600"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z"
              />
            </svg>
          </div>
          <p className="text-sm text-slate-600 font-medium mb-1">
            No Tallula runs yet
          </p>
          <p className="text-xs text-slate-400 max-w-md mx-auto">
            Configure the parameters above and click &quot;Run Tallula&quot; to
            discover novel drug repurposing candidates hidden in the evidence
            landscape.
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Discovery card
// ---------------------------------------------------------------

function DiscoveryCard({
  discovery,
  expanded,
  onToggle,
}: {
  discovery: TallulaDiscovery;
  expanded: boolean;
  onToggle: () => void;
}) {
  const cfg = CLASS_CONFIG[discovery.discovery_class] ?? CLASS_CONFIG.weak;
  const metrics = {
    ubiquity: discovery.ubiquity,
    resonance: discovery.resonance,
    fragility: discovery.fragility_index,
  };

  return (
    <div
      className={`rounded-lg border bg-white shadow-sm transition-all ${
        expanded ? `${cfg.border}` : "border-slate-200"
      }`}
    >
      {/* Header row */}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-4 px-4 py-3 text-left"
      >
        <div className="flex items-center gap-3 min-w-0">
          <span
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.color} ${cfg.border} border`}
          >
            {cfg.label}
          </span>
          <span className="text-sm font-medium text-slate-800 truncate">
            {discovery.title || `Hypothesis #${discovery.hypothesis_id}`}
          </span>
          {discovery.critical_dimension && (
            <span className="hidden sm:inline text-xs text-slate-400">
              Key:{" "}
              {DIMENSION_LABELS[discovery.critical_dimension] ??
                discovery.critical_dimension}
            </span>
          )}
        </div>
        <div className="flex items-center gap-4 flex-shrink-0">
          <div className="hidden sm:flex gap-3 text-xs text-slate-500">
            <span>
              Res: <strong>{metrics.resonance.toFixed(1)}</strong>
            </span>
            <span>
              Ubiq: <strong>{(metrics.ubiquity * 100).toFixed(0)}%</strong>
            </span>
            <span>
              Frag: <strong>{metrics.fragility.toFixed(2)}</strong>
            </span>
          </div>
          <span className="text-sm font-semibold text-slate-700">
            {discovery.score_max?.toFixed(1) ?? "—"}
          </span>
          <svg
            className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m19.5 8.25-7.5 7.5-7.5-7.5"
            />
          </svg>
        </div>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-slate-100 px-4 py-4 space-y-4">
          {/* Metric row */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MetricPill
              label="Deterministic Score"
              value={discovery.deterministic_score.toFixed(1)}
            />
            <MetricPill
              label="Mean (across lenses)"
              value={discovery.score_mean?.toFixed(1) ?? "—"}
            />
            <MetricPill
              label="Max"
              value={discovery.score_max?.toFixed(1) ?? "—"}
            />
            <MetricPill
              label="Std Dev"
              value={discovery.score_std?.toFixed(2) ?? "—"}
            />
          </div>

          {/* Ablation impacts */}
          {discovery.ablation_impacts &&
            Object.keys(discovery.ablation_impacts).length > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-slate-600 mb-2">
                  Dimension Ablation Impact
                </h4>
                <div className="space-y-1.5">
                  {Object.entries(discovery.ablation_impacts)
                    .sort(([, a], [, b]) => b - a)
                    .map(([dim, impact]) => (
                      <div key={dim} className="flex items-center gap-2">
                        <span className="text-xs text-slate-500 w-36 truncate">
                          {DIMENSION_LABELS[dim] ?? dim}
                        </span>
                        <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              dim === discovery.critical_dimension
                                ? "bg-red-400"
                                : "bg-slate-300"
                            }`}
                            style={{ width: `${Math.min(impact * 100, 100)}%` }}
                          />
                        </div>
                        <span className="text-xs text-slate-500 w-12 text-right">
                          {(impact * 100).toFixed(1)}%
                        </span>
                      </div>
                    ))}
                </div>
              </div>
            )}

          {/* Resonance narrative */}
          {discovery.resonance_profile?.narrative && (
            <div className="rounded-md bg-slate-50 border border-slate-100 px-4 py-3">
              <h4 className="text-xs font-semibold text-slate-600 mb-1">
                Resonance Explanation
              </h4>
              <p className="text-sm text-slate-700 leading-relaxed">
                {discovery.resonance_profile.narrative}
              </p>
            </div>
          )}

          {/* Activation dimensions */}
          {discovery.resonance_profile?.activation_dimensions && (
            <div>
              <h4 className="text-xs font-semibold text-slate-600 mb-2">
                Activation Dimensions
              </h4>
              <div className="flex flex-wrap gap-2">
                {discovery.resonance_profile.activation_dimensions
                  .filter((a) => a.activation_strength > 0.005)
                  .map((a) => (
                    <span
                      key={a.dimension}
                      className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2.5 py-1 text-xs text-amber-700"
                    >
                      {DIMENSION_LABELS[a.dimension] ?? a.dimension}
                      <span className="font-semibold">
                        +{(a.activation_strength * 100).toFixed(1)}
                      </span>
                    </span>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MetricPill({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md bg-slate-50 border border-slate-100 px-3 py-2">
      <p className="text-[11px] text-slate-400">{label}</p>
      <p className="text-sm font-semibold text-slate-800">{value}</p>
    </div>
  );
}
