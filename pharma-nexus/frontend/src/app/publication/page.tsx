"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";
import type {
  ReadinessResult,
  ReadinessCheck,
  SoftwareSnapshot,
  FairnessStratum,
  CompletenessData,
} from "@/types";

// ---------------------------------------------------------------------------
// Types for API responses
// ---------------------------------------------------------------------------

interface MethodsData {
  generated_at: string;
  data_sources: {
    summary: Record<string, number>;
    per_source: {
      source: string;
      version: string | null;
      downloaded_at: string | null;
      records_processed: number;
      records_filtered: number | null;
      api_url: string | null;
    }[];
  };
  hypothesis_generation: {
    total_hypotheses: number;
    distinct_cancer_types: number;
    distinct_drugs: number;
    combination_hypotheses: number;
    generation_batches: number;
  };
  scoring: {
    dimensions: string[];
    active_weights: {
      preset_name: string;
      weights: Record<string, number>;
      created_at: string | null;
    } | null;
    score_distribution: Record<string, number | null>;
    evidence_strength_distribution: Record<string, number>;
  };
}

interface FairnessData {
  generated_at: string;
  by_cancer_type: FairnessStratum[];
  by_drug_status: FairnessStratum[];
  strength_by_cancer: Record<string, Record<string, number>>;
}

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------

type TabKey = "readiness" | "methods" | "completeness" | "fairness" | "software";

const TABS: { key: TabKey; label: string }[] = [
  { key: "readiness", label: "Readiness" },
  { key: "methods", label: "Methods" },
  { key: "completeness", label: "Completeness" },
  { key: "fairness", label: "Fairness" },
  { key: "software", label: "Software" },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PublicationPage() {
  const [tab, setTab] = useState<TabKey>("readiness");
  const [readiness, setReadiness] = useState<ReadinessResult | null>(null);
  const [methods, setMethods] = useState<MethodsData | null>(null);
  const [completeness, setCompleteness] = useState<CompletenessData | null>(null);
  const [fairness, setFairness] = useState<FairnessData | null>(null);
  const [software, setSoftware] = useState<SoftwareSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (t: TabKey) => {
    setLoading(true);
    setError(null);
    try {
      switch (t) {
        case "readiness": {
          const r = await fetchApi<ReadinessResult>("/api/publication/readiness");
          setReadiness(r);
          break;
        }
        case "methods": {
          const m = await fetchApi<MethodsData>("/api/publication/methods");
          setMethods(m);
          break;
        }
        case "completeness": {
          const c = await fetchApi<CompletenessData>("/api/publication/completeness");
          setCompleteness(c);
          break;
        }
        case "fairness": {
          const f = await fetchApi<FairnessData>("/api/publication/fairness");
          setFairness(f);
          break;
        }
        case "software": {
          const s = await fetchApi<{ snapshots: SoftwareSnapshot[] }>(
            "/api/publication/software-versions"
          );
          setSoftware(s.snapshots);
          break;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(tab);
  }, [tab, load]);

  const captureSnapshot = async () => {
    setCapturing(true);
    try {
      await fetchApi("/api/publication/software-versions?label=manual", {
        method: "POST",
      });
      await load("software");
    } catch {
      setError("Failed to capture software snapshot");
    } finally {
      setCapturing(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
          Publication Tools
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          Readiness checklist, auto-generated methods, data completeness, fairness analysis, and software versioning
        </p>
      </div>

      {/* Tab bar */}
      <div className="border-b border-slate-200 dark:border-slate-700">
        <nav className="flex gap-1" aria-label="Publication tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                tab === t.key
                  ? "border-blue-600 text-blue-600 dark:text-blue-400 dark:border-blue-400"
                  : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-900/20 dark:border-red-800 p-4 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading...
        </div>
      )}

      {/* Tab content */}
      {!loading && tab === "readiness" && readiness && (
        <ReadinessTab data={readiness} onRefresh={() => load("readiness")} />
      )}
      {!loading && tab === "methods" && methods && (
        <MethodsTab data={methods} />
      )}
      {!loading && tab === "completeness" && completeness && (
        <CompletenessTab data={completeness} />
      )}
      {!loading && tab === "fairness" && fairness && (
        <FairnessTab data={fairness} />
      )}
      {!loading && tab === "software" && (
        <SoftwareTab
          snapshots={software}
          onCapture={captureSnapshot}
          capturing={capturing}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Readiness Tab
// ---------------------------------------------------------------------------

function ReadinessTab({ data, onRefresh }: { data: ReadinessResult; onRefresh: () => void }) {
  const { summary, checks } = data;

  const ringColor =
    summary.percentage === 100
      ? "text-emerald-500"
      : summary.percentage >= 70
        ? "text-amber-500"
        : "text-red-500";

  return (
    <div className="space-y-6">
      {/* Summary ring */}
      <div className="flex items-center gap-6 bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-6">
        <div className="relative w-24 h-24 flex-shrink-0">
          <svg className="w-24 h-24 -rotate-90" viewBox="0 0 36 36">
            <path
              className="text-slate-200 dark:text-slate-700"
              d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
            />
            <path
              className={ringColor}
              d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeDasharray={`${summary.percentage}, 100`}
              strokeLinecap="round"
            />
          </svg>
          <span className="absolute inset-0 flex items-center justify-center text-xl font-bold text-slate-900 dark:text-white">
            {summary.percentage}%
          </span>
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            {summary.ready ? "Publication Ready" : "Not Yet Ready"}
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {summary.passed} of {summary.total} checks passed
          </p>
          <button
            onClick={onRefresh}
            className="mt-2 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Checklist */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-700">
        {checks.map((check: ReadinessCheck) => (
          <div
            key={check.key}
            className="flex items-start gap-3 px-5 py-4"
          >
            <span className={`mt-0.5 flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center ${
              check.passed
                ? "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
                : "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400"
            }`}>
              {check.passed ? (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
              ) : (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              )}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-900 dark:text-white">
                {check.label}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {check.detail}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Methods Tab
// ---------------------------------------------------------------------------

function MethodsTab({ data }: { data: MethodsData }) {
  const { data_sources, hypothesis_generation, scoring } = data;

  return (
    <div className="space-y-6">
      {/* Data Sources */}
      <Section title="Data Sources">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          {Object.entries(data_sources.summary).map(([key, value]) => (
            <div
              key={key}
              className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3 text-center"
            >
              <p className="text-lg font-bold text-slate-900 dark:text-white">
                {(value as number).toLocaleString()}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 capitalize">
                {key.replace(/_/g, " ")}
              </p>
            </div>
          ))}
        </div>

        {data_sources.per_source.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs font-medium text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
                  <th className="pb-2 pr-4">Source</th>
                  <th className="pb-2 pr-4">Version</th>
                  <th className="pb-2 pr-4">Downloaded</th>
                  <th className="pb-2 pr-4 text-right">Records</th>
                  <th className="pb-2 text-right">Filtered</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                {data_sources.per_source.map((src) => (
                  <tr key={src.source} className="text-slate-700 dark:text-slate-300">
                    <td className="py-2 pr-4 font-medium">{src.source}</td>
                    <td className="py-2 pr-4">
                      {src.version ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                          {src.version}
                        </span>
                      ) : (
                        <span className="text-slate-400">--</span>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-xs">
                      {src.downloaded_at
                        ? new Date(src.downloaded_at).toLocaleDateString()
                        : "--"}
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {src.records_processed.toLocaleString()}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {src.records_filtered != null
                        ? src.records_filtered.toLocaleString()
                        : "--"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Hypothesis Generation */}
      <Section title="Hypothesis Generation">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MetricCard label="Total hypotheses" value={hypothesis_generation.total_hypotheses.toLocaleString()} />
          <MetricCard label="Cancer types" value={hypothesis_generation.distinct_cancer_types.toString()} />
          <MetricCard label="Distinct drugs" value={hypothesis_generation.distinct_drugs.toLocaleString()} />
          <MetricCard label="Combinations" value={hypothesis_generation.combination_hypotheses.toLocaleString()} />
          <MetricCard label="Batches" value={hypothesis_generation.generation_batches.toString()} />
        </div>
      </Section>

      {/* Scoring */}
      <Section title="Scoring Configuration">
        {scoring.active_weights && (
          <div className="mb-4">
            <p className="text-sm text-slate-600 dark:text-slate-300 mb-2">
              Active preset: <span className="font-semibold">{scoring.active_weights.preset_name}</span>
            </p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(scoring.active_weights.weights).map(([dim, w]) => (
                <span
                  key={dim}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300"
                >
                  <span className="capitalize">{dim.replace(/_/g, " ")}</span>
                  <span className="font-bold">{((w as number) * 100).toFixed(0)}%</span>
                </span>
              ))}
            </div>
          </div>
        )}

        <h4 className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-2">
          Score Distribution
        </h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(scoring.score_distribution).map(([key, val]) => (
            <div key={key} className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3 text-center">
              <p className="text-lg font-bold text-slate-900 dark:text-white tabular-nums">
                {val != null ? val : "--"}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 uppercase">
                {key}
              </p>
            </div>
          ))}
        </div>

        {Object.keys(scoring.evidence_strength_distribution).length > 0 && (
          <div className="mt-4">
            <h4 className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-2">
              Evidence Strength Distribution
            </h4>
            <div className="flex gap-3 flex-wrap">
              {Object.entries(scoring.evidence_strength_distribution).map(([strength, count]) => (
                <StrengthPill key={strength} strength={strength} count={count as number} />
              ))}
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Completeness Tab
// ---------------------------------------------------------------------------

function CompletenessTab({ data }: { data: CompletenessData }) {
  const { entities, dimension_completeness, ingestion_freshness } = data;

  return (
    <div className="space-y-6">
      {/* Entity completeness */}
      <Section title="Entity Completeness">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Drugs</h4>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{entities.drugs.total.toLocaleString()}</p>
            <div className="mt-2 space-y-1">
              <ProgressStat label="With mechanism" value={entities.drugs.with_mechanism} total={entities.drugs.total} />
              <ProgressStat label="With targets" value={entities.drugs.with_targets} total={entities.drugs.total} />
            </div>
          </div>
          <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Targets</h4>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{entities.targets.total.toLocaleString()}</p>
          </div>
          <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Cancer Types</h4>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{entities.cancer_types.total.toLocaleString()}</p>
          </div>
        </div>
      </Section>

      {/* Scoring dimension completeness */}
      <Section title="Scoring Dimension Completeness">
        <div className="space-y-2">
          {Object.entries(dimension_completeness).map(([dim, info]) => (
            <div key={dim} className="flex items-center gap-3">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-300 w-44 capitalize truncate">
                {dim.replace(/_/g, " ")}
              </span>
              <div className="flex-1 h-2.5 bg-slate-100 dark:bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    info.percentage >= 80
                      ? "bg-emerald-500"
                      : info.percentage >= 40
                        ? "bg-amber-500"
                        : "bg-red-500"
                  }`}
                  style={{ width: `${Math.min(info.percentage, 100)}%` }}
                />
              </div>
              <span className="text-xs tabular-nums text-slate-500 dark:text-slate-400 w-14 text-right">
                {info.percentage}%
              </span>
            </div>
          ))}
        </div>
      </Section>

      {/* Ingestion freshness */}
      {Object.keys(ingestion_freshness).length > 0 && (
        <Section title="Ingestion Freshness">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs font-medium text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
                  <th className="pb-2 pr-4">Source</th>
                  <th className="pb-2 pr-4">Last Ingested</th>
                  <th className="pb-2 text-right">Total Records</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                {Object.entries(ingestion_freshness).map(([source, info]) => (
                  <tr key={source} className="text-slate-700 dark:text-slate-300">
                    <td className="py-2 pr-4 font-medium">{source}</td>
                    <td className="py-2 pr-4 text-xs">
                      {info.last_ingested
                        ? new Date(info.last_ingested).toLocaleString()
                        : "--"}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {info.total_records_ever.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fairness Tab
// ---------------------------------------------------------------------------

function FairnessTab({ data }: { data: FairnessData }) {
  return (
    <div className="space-y-6">
      {/* By cancer type */}
      <Section title="Score Distribution by Cancer Type">
        {data.by_cancer_type.length === 0 ? (
          <p className="text-sm text-slate-500">No hypotheses found.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs font-medium text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
                  <th className="pb-2 pr-4">Cancer Type</th>
                  <th className="pb-2 pr-4">TCGA</th>
                  <th className="pb-2 pr-4 text-right">Count</th>
                  <th className="pb-2 pr-4 text-right">Mean</th>
                  <th className="pb-2 pr-4 text-right">Std</th>
                  <th className="pb-2 pr-4 text-right">Min</th>
                  <th className="pb-2 text-right">Max</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                {data.by_cancer_type.map((row) => (
                  <tr key={row.tcga_code} className="text-slate-700 dark:text-slate-300">
                    <td className="py-2 pr-4 font-medium truncate max-w-[200px]">
                      {row.cancer_type}
                    </td>
                    <td className="py-2 pr-4">
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono bg-slate-100 dark:bg-slate-700">
                        {row.tcga_code}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">{row.hypothesis_count}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{row.mean_score}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{row.std_score}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{row.min_score}</td>
                    <td className="py-2 text-right tabular-nums">{row.max_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* By drug status */}
      <Section title="Score Distribution by Drug Approval Status">
        {data.by_drug_status.length === 0 ? (
          <p className="text-sm text-slate-500">No hypotheses found.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.by_drug_status.map((row) => (
              <div
                key={row.drug_status}
                className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                    row.drug_status === "approved"
                      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                      : row.drug_status === "experimental"
                        ? "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                        : "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300"
                  }`}>
                    {row.drug_status}
                  </span>
                  <span className="text-xs text-slate-500">{row.hypothesis_count} hyps</span>
                </div>
                <p className="text-2xl font-bold text-slate-900 dark:text-white tabular-nums">
                  {row.mean_score}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  mean score (std: {row.std_score})
                </p>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Strength by cancer heatmap-style */}
      {Object.keys(data.strength_by_cancer).length > 0 && (
        <Section title="Evidence Strength by Cancer Type">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left font-medium text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
                  <th className="pb-2 pr-4">TCGA</th>
                  <th className="pb-2 pr-2 text-center">Strong</th>
                  <th className="pb-2 pr-2 text-center">Moderate</th>
                  <th className="pb-2 pr-2 text-center">Suggestive</th>
                  <th className="pb-2 text-center">Speculative</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                {Object.entries(data.strength_by_cancer).map(([code, dist]) => (
                  <tr key={code} className="text-slate-700 dark:text-slate-300">
                    <td className="py-1.5 pr-4 font-mono">{code}</td>
                    <td className="py-1.5 pr-2 text-center">
                      <CellCount value={dist.strong} color="emerald" />
                    </td>
                    <td className="py-1.5 pr-2 text-center">
                      <CellCount value={dist.moderate} color="blue" />
                    </td>
                    <td className="py-1.5 pr-2 text-center">
                      <CellCount value={dist.suggestive} color="amber" />
                    </td>
                    <td className="py-1.5 text-center">
                      <CellCount value={dist.speculative} color="slate" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Software Tab
// ---------------------------------------------------------------------------

function SoftwareTab({
  snapshots,
  onCapture,
  capturing,
}: {
  snapshots: SoftwareSnapshot[];
  onCapture: () => void;
  capturing: boolean;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {snapshots.length} snapshot(s) recorded
        </p>
        <button
          onClick={onCapture}
          disabled={capturing}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50 transition-colors"
        >
          {capturing ? "Capturing..." : "Capture Current Versions"}
        </button>
      </div>

      {snapshots.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-slate-500 dark:text-slate-400 text-sm">
            No software version snapshots yet. Capture one to start tracking.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {snapshots.map((snap) => (
            <div
              key={snap.id}
              className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-hidden"
            >
              <button
                onClick={() => setExpanded(expanded === snap.id ? null : snap.id)}
                className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">
                    {snap.snapshot_label}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                    Python {snap.python_version.split(" ")[0]} &middot;{" "}
                    {snap.created_at ? new Date(snap.created_at).toLocaleString() : "--"}
                    {snap.pipeline_run_id && ` \u00B7 Pipeline run #${snap.pipeline_run_id}`}
                  </p>
                </div>
                <svg
                  className={`w-4 h-4 text-slate-400 transition-transform ${expanded === snap.id ? "rotate-180" : ""}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                </svg>
              </button>

              {expanded === snap.id && (
                <div className="px-5 pb-4 border-t border-slate-100 dark:border-slate-700">
                  <div className="pt-3 grid grid-cols-2 md:grid-cols-3 gap-2">
                    {Object.entries(snap.packages).map(([pkg, ver]) => (
                      <div key={pkg} className="flex items-center justify-between text-xs py-1 px-2 rounded bg-slate-50 dark:bg-slate-700/50">
                        <span className="text-slate-600 dark:text-slate-300 font-mono">{pkg}</span>
                        <span className="text-slate-900 dark:text-white font-semibold ml-2">
                          {ver || "n/a"}
                        </span>
                      </div>
                    ))}
                  </div>
                  {snap.platform_info && (
                    <div className="mt-3 text-xs text-slate-500 dark:text-slate-400">
                      {snap.platform_info.os} {snap.platform_info.architecture} &middot;{" "}
                      {snap.platform_info.python_implementation}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-4">
        {title}
      </h3>
      {children}
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-3 text-center">
      <p className="text-lg font-bold text-slate-900 dark:text-white tabular-nums">{value}</p>
      <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
    </div>
  );
}

function ProgressStat({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-500 dark:text-slate-400 w-28">{label}</span>
      <div className="flex-1 h-1.5 bg-slate-100 dark:bg-slate-600 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-slate-600 dark:text-slate-300 w-8 text-right">{pct}%</span>
    </div>
  );
}

function StrengthPill({ strength, count }: { strength: string; count: number }) {
  const colors: Record<string, string> = {
    strong: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
    moderate: "bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    suggestive: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
    speculative: "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ${colors[strength] || colors.speculative}`}>
      {strength}
      <span className="font-bold tabular-nums">{count.toLocaleString()}</span>
    </span>
  );
}

function CellCount({ value, color }: { value: number | undefined; color: string }) {
  if (!value) return <span className="text-slate-300 dark:text-slate-600">0</span>;
  const colorMap: Record<string, string> = {
    emerald: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    blue: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
    amber: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    slate: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400",
  };
  return (
    <span className={`inline-flex items-center justify-center w-8 h-6 rounded text-xs font-bold tabular-nums ${colorMap[color]}`}>
      {value}
    </span>
  );
}
