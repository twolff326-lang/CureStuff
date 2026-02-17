"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi } from "@/lib/api";
import type { Hypothesis, CancerType } from "@/types";

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface AnalysisSummary {
  id: number;
  analysis_type: string;
  model_used: string;
  created_at: string;
}

interface ReportMeta {
  filename: string;
  report_type: string;
  entity_id: number | null;
  file_size_bytes: number;
  generated_at: string;
}

const ANALYSIS_TYPES = [
  { key: "narrative", label: "Narrative", desc: "Mechanistic explanation of the biology" },
  { key: "critique", label: "Critique", desc: "Peer-review: weaknesses, gaps, risks" },
  { key: "literature_synthesis", label: "Literature", desc: "Synthesis of supporting papers" },
  { key: "experiment_design", label: "Experiments", desc: "Validation plan: in-vitro → clinical" },
  { key: "confidence", label: "Confidence", desc: "Confidence score with precedent analysis" },
  { key: "comparative", label: "Comparative", desc: "Comparison vs. similar hypotheses" },
];

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ------------------------------------------------------------------
// Page
// ------------------------------------------------------------------

export default function ReportsPage() {
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [cancers, setCancers] = useState<CancerType[]>([]);
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Hypothesis | null>(null);
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [generating, setGenerating] = useState<Record<string, boolean>>({});
  const [reportGenerating, setReportGenerating] = useState<Record<string, boolean>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});

  // Load top hypotheses + cancers + existing reports
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [hypRes, cancerRes, reportsRes] = await Promise.allSettled([
        fetchApi<{ hypotheses: Hypothesis[] }>("/api/hypotheses/top?limit=30"),
        fetchApi<{ cancer_types: CancerType[]; total: number }>("/api/cancer-types?per_page=50"),
        fetchApi<ReportMeta[]>("/api/reports/list"),
      ]);
      if (hypRes.status === "fulfilled") setHypotheses(hypRes.value.hypotheses);
      if (cancerRes.status === "fulfilled") setCancers(cancerRes.value.cancer_types);
      if (reportsRes.status === "fulfilled") setReports(reportsRes.value);
    } catch { /* degrade */ }
    setLoading(false);
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // Load analyses for selected hypothesis
  const selectHypothesis = useCallback(async (h: Hypothesis) => {
    setSelected(h);
    setAnalyses([]);
    try {
      const res = await fetchApi<{ analyses: AnalysisSummary[] }>(
        `/api/llm-analysis/hypothesis/${h.id}/analyses`
      );
      setAnalyses(res.analyses);
    } catch { /* no analyses yet */ }
  }, []);

  // Run a single analysis type
  const runAnalysis = async (hypothesisId: number, type: string) => {
    const gKey = `${hypothesisId}-${type}`;
    setGenerating((p) => ({ ...p, [gKey]: true }));
    setMessages((p) => ({ ...p, [gKey]: "" }));
    try {
      const endpoint = type === "literature_synthesis" ? "literature"
        : type === "experiment_design" ? "experiments"
        : type;
      await fetchApi(`/api/llm-analysis/hypothesis/${hypothesisId}/${endpoint}`, {
        method: "POST",
      });
      setMessages((p) => ({ ...p, [gKey]: "Done" }));
      if (selected?.id === hypothesisId) selectHypothesis(selected);
    } catch {
      setMessages((p) => ({ ...p, [gKey]: "Failed" }));
    } finally {
      setGenerating((p) => ({ ...p, [gKey]: false }));
    }
  };

  // Run all 6 analyses
  const runFullAnalysis = async (hypothesisId: number) => {
    const gKey = `full-${hypothesisId}`;
    setGenerating((p) => ({ ...p, [gKey]: true }));
    try {
      await fetchApi(`/api/llm-analysis/hypothesis/${hypothesisId}/full`, {
        method: "POST",
      });
      setMessages((p) => ({ ...p, [gKey]: "All 6 analyses complete" }));
      if (selected?.id === hypothesisId) selectHypothesis(selected);
    } catch {
      setMessages((p) => ({ ...p, [gKey]: "Failed" }));
    } finally {
      setGenerating((p) => ({ ...p, [gKey]: false }));
    }
  };

  // Generate PDF reports
  const generateReport = async (type: string, entityId?: number) => {
    const rKey = `${type}-${entityId ?? "all"}`;
    setReportGenerating((p) => ({ ...p, [rKey]: true }));
    try {
      let url = "";
      if (type === "hypothesis" && entityId) url = `/api/reports/hypothesis/${entityId}`;
      else if (type === "cancer" && entityId) url = `/api/reports/cancer/${entityId}`;
      else if (type === "novel") url = "/api/reports/novel-discoveries";
      else if (type === "executive") url = "/api/reports/executive-summary";

      const res = await fetch(`${API_URL}${url}`, { method: "POST" });
      if (res.ok) {
        const blob = await res.blob();
        const filename = res.headers.get("content-disposition")?.split("filename=")[1]?.replace(/"/g, "")
          ?? `${type}_report.pdf`;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
        setMessages((p) => ({ ...p, [rKey]: "Downloaded" }));
        loadData(); // refresh report list
      } else {
        setMessages((p) => ({ ...p, [rKey]: `Error: ${res.status}` }));
      }
    } catch {
      setMessages((p) => ({ ...p, [rKey]: "Failed" }));
    } finally {
      setReportGenerating((p) => ({ ...p, [rKey]: false }));
    }
  };

  // Export CSV/JSON
  const exportData = async (format: "csv" | "json", hypothesisId?: number) => {
    try {
      const url = format === "csv"
        ? `${API_URL}/api/reports/export/hypotheses/csv?min_score=30`
        : `${API_URL}/api/reports/export/hypothesis/${hypothesisId}/json`;
      const res = await fetch(url);
      if (res.ok) {
        const blob = await res.blob();
        const ext = format === "csv" ? "csv" : "json";
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `pharma_nexus_export.${ext}`;
        a.click();
        URL.revokeObjectURL(a.href);
      }
    } catch { /* silent */ }
  };

  const hasAnalysis = (type: string) => analyses.some((a) => a.analysis_type === type);
  const analysisCount = selected ? analyses.length : 0;
  const isFullyAnalyzed = analysisCount >= 6;

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 mb-2">Reports & Findings</h1>
      <p className="text-sm text-slate-500 mb-6">
        Generate publication-quality reports with LLM-powered analysis. Run the analysis pipeline, then generate PDFs for researchers and funders.
      </p>

      {/* Global report actions */}
      <div className="bg-gradient-to-br from-slate-50 to-blue-50 rounded-lg border border-slate-200 p-5 mb-8">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Portfolio Reports</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <ReportCard
            title="Executive Summary"
            desc="2-3 page overview for stakeholders"
            onClick={() => generateReport("executive")}
            loading={!!reportGenerating["executive-all"]}
            msg={messages["executive-all"]}
          />
          <ReportCard
            title="Novel Discoveries"
            desc="High-novelty findings across all cancers"
            onClick={() => generateReport("novel")}
            loading={!!reportGenerating["novel-all"]}
            msg={messages["novel-all"]}
            accent
          />
          <ReportCard
            title="Export All (CSV)"
            desc="Bulk data for external analysis"
            onClick={() => exportData("csv")}
            loading={false}
          />
          {cancers.length > 0 && (
            <div>
              <label className="block text-xs text-slate-500 mb-1">Cancer Summary</label>
              <select
                onChange={(e) => { if (e.target.value) generateReport("cancer", Number(e.target.value)); }}
                className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm bg-white"
                defaultValue=""
              >
                <option value="" disabled>Select cancer type...</option>
                {cancers.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: hypothesis list */}
        <div className="lg:col-span-1">
          <h2 className="text-sm font-semibold text-slate-700 mb-3">Top Hypotheses</h2>
          {loading && (
            <div className="text-center py-8">
              <div className="inline-block h-5 w-5 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
            </div>
          )}
          <div className="space-y-1.5 max-h-[700px] overflow-y-auto">
            {hypotheses.map((h) => (
              <button
                key={h.id}
                onClick={() => selectHypothesis(h)}
                className={`w-full text-left px-3 py-2.5 rounded-lg border text-sm transition-colors ${
                  selected?.id === h.id
                    ? "bg-blue-50 border-blue-300"
                    : "bg-white border-slate-200 hover:border-slate-300"
                }`}
              >
                <div className="flex items-center justify-between mb-0.5">
                  <span className="font-medium text-slate-800 truncate mr-2">
                    {h.drug?.name ?? "Drug"} &rarr; {h.cancer_type?.name ?? "Cancer"}
                  </span>
                  <span className="text-xs font-bold text-slate-600 shrink-0">
                    {h.adjusted_score ?? h.composite_score}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <StrengthBadge strength={h.evidence_strength} />
                  {h.llm_confidence_score != null && (
                    <span className="text-xs text-violet-600">{h.llm_confidence_score}% conf</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Right: analysis pipeline + actions */}
        <div className="lg:col-span-2">
          {!selected && (
            <div className="bg-white rounded-lg border border-slate-200 p-12 text-center text-sm text-slate-400">
              Select a hypothesis from the list to view its analysis pipeline and generate reports.
            </div>
          )}

          {selected && (
            <div className="space-y-5">
              {/* Header */}
              <div className="bg-white rounded-lg border border-slate-200 p-5">
                <h3 className="font-bold text-slate-900 mb-1">{selected.title}</h3>
                <p className="text-sm text-slate-500 mb-3">{selected.summary}</p>
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-sm">
                    Score: <strong>{selected.adjusted_score ?? selected.composite_score}</strong>
                    {selected.adjusted_score && selected.adjusted_score !== selected.composite_score && (
                      <span className="text-xs text-slate-400 ml-1">(raw {selected.composite_score})</span>
                    )}
                  </span>
                  <StrengthBadge strength={selected.evidence_strength} />
                  {selected.llm_recommendation && (
                    <RecommendationBadge rec={selected.llm_recommendation} />
                  )}
                  <span className="text-xs text-slate-400">
                    {analysisCount}/6 analyses complete
                  </span>
                </div>
              </div>

              {/* Analysis pipeline */}
              <div className="bg-white rounded-lg border border-slate-200 p-5">
                <div className="flex items-center justify-between mb-4">
                  <h4 className="text-sm font-semibold text-slate-700">Analysis Pipeline</h4>
                  <button
                    onClick={() => runFullAnalysis(selected.id)}
                    disabled={!!generating[`full-${selected.id}`] || isFullyAnalyzed}
                    className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
                  >
                    {generating[`full-${selected.id}`]
                      ? "Running all 6..."
                      : isFullyAnalyzed
                      ? "All complete"
                      : "Run Full Analysis"}
                  </button>
                </div>
                {messages[`full-${selected.id}`] && (
                  <p className="text-xs text-emerald-600 mb-3">{messages[`full-${selected.id}`]}</p>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {ANALYSIS_TYPES.map((at) => {
                    const done = hasAnalysis(at.key);
                    const gKey = `${selected.id}-${at.key}`;
                    const isRunning = !!generating[gKey];
                    return (
                      <div
                        key={at.key}
                        className={`rounded-lg border p-3 ${
                          done ? "bg-emerald-50 border-emerald-200" : "bg-white border-slate-200"
                        }`}
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-slate-800">{at.label}</span>
                          {done ? (
                            <span className="text-xs font-medium text-emerald-700 bg-emerald-100 px-1.5 py-0.5 rounded">Done</span>
                          ) : (
                            <button
                              onClick={() => runAnalysis(selected.id, at.key)}
                              disabled={isRunning}
                              className="text-xs text-blue-600 hover:text-blue-800 disabled:text-slate-400"
                            >
                              {isRunning ? "Running..." : "Run"}
                            </button>
                          )}
                        </div>
                        <p className="text-xs text-slate-500">{at.desc}</p>
                        {messages[gKey] && (
                          <p className={`text-xs mt-1 ${messages[gKey] === "Done" ? "text-emerald-600" : "text-red-500"}`}>
                            {messages[gKey]}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Report generation */}
              <div className="bg-white rounded-lg border border-slate-200 p-5">
                <h4 className="text-sm font-semibold text-slate-700 mb-3">Generate Reports</h4>
                <p className="text-xs text-slate-400 mb-4">
                  Reports include all completed analyses. Run the pipeline first for comprehensive output.
                </p>
                <div className="flex flex-wrap gap-3">
                  <button
                    onClick={() => generateReport("hypothesis", selected.id)}
                    disabled={!!reportGenerating[`hypothesis-${selected.id}`]}
                    className="flex-1 min-w-[160px] px-4 py-2.5 bg-slate-900 text-white text-sm rounded-md hover:bg-slate-800 disabled:opacity-50"
                  >
                    {reportGenerating[`hypothesis-${selected.id}`]
                      ? "Generating PDF..."
                      : "Download PDF Report"}
                  </button>
                  <button
                    onClick={() => exportData("json", selected.id)}
                    className="px-4 py-2.5 border border-slate-300 text-sm text-slate-700 rounded-md hover:bg-slate-50"
                  >
                    Export JSON
                  </button>
                </div>
                {messages[`hypothesis-${selected.id}`] && (
                  <p className="text-xs text-emerald-600 mt-2">{messages[`hypothesis-${selected.id}`]}</p>
                )}
              </div>

              {/* Score dimensions */}
              {selected.dimension_scores && (
                <div className="bg-white rounded-lg border border-slate-200 p-5">
                  <h4 className="text-sm font-semibold text-slate-700 mb-3">Evidence Dimensions</h4>
                  <div className="space-y-2">
                    {Object.entries(selected.dimension_scores).map(([dim, score]) => (
                      <DimensionBar key={dim} label={dim} score={score} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Existing reports */}
      {reports.length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold text-slate-700 mb-3">Generated Reports</h2>
          <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
            <table className="min-w-full divide-y divide-slate-100">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase">File</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase">Type</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Size</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Generated</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {reports.map((r) => (
                  <tr key={r.filename} className="hover:bg-slate-50">
                    <td className="px-4 py-2 text-sm text-slate-700 font-mono text-xs">{r.filename}</td>
                    <td className="px-4 py-2 text-sm text-slate-600">{r.report_type}</td>
                    <td className="px-4 py-2 text-sm text-slate-500 text-right">{formatBytes(r.file_size_bytes)}</td>
                    <td className="px-4 py-2 text-xs text-slate-400 text-right">{formatDate(r.generated_at)}</td>
                    <td className="px-4 py-2 text-right">
                      <a
                        href={`${API_URL}/api/reports/download/${r.filename}`}
                        className="text-xs text-blue-600 hover:text-blue-800"
                      >
                        Download
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------
// Sub-components
// ------------------------------------------------------------------

function ReportCard({
  title, desc, onClick, loading, msg, accent,
}: {
  title: string; desc: string; onClick: () => void; loading: boolean; msg?: string; accent?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`text-left p-3 rounded-lg border transition-colors ${
        accent
          ? "bg-emerald-50 border-emerald-200 hover:bg-emerald-100"
          : "bg-white border-slate-200 hover:bg-slate-50"
      } disabled:opacity-50`}
    >
      <p className="text-sm font-medium text-slate-800">{title}</p>
      <p className="text-xs text-slate-500 mt-0.5">{desc}</p>
      {loading && <p className="text-xs text-blue-600 mt-1">Generating...</p>}
      {msg && <p className="text-xs text-emerald-600 mt-1">{msg}</p>}
    </button>
  );
}

function StrengthBadge({ strength }: { strength: string }) {
  const colors: Record<string, string> = {
    strong: "bg-emerald-100 text-emerald-800",
    moderate: "bg-blue-100 text-blue-800",
    suggestive: "bg-amber-100 text-amber-800",
    speculative: "bg-slate-100 text-slate-600",
  };
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${colors[strength] ?? colors.speculative}`}>
      {strength}
    </span>
  );
}

function RecommendationBadge({ rec }: { rec: string }) {
  const styles: Record<string, string> = {
    proceed_immediately: "bg-emerald-100 text-emerald-800",
    proceed_with_caution: "bg-blue-100 text-blue-800",
    additional_data_needed: "bg-amber-100 text-amber-800",
    deprioritize: "bg-red-100 text-red-800",
  };
  const labels: Record<string, string> = {
    proceed_immediately: "Proceed",
    proceed_with_caution: "Caution",
    additional_data_needed: "More data needed",
    deprioritize: "Deprioritize",
  };
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${styles[rec] ?? "bg-slate-100 text-slate-600"}`}>
      {labels[rec] ?? rec}
    </span>
  );
}

function DimensionBar({ label, score }: { label: string; score: number }) {
  const formatted = label.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const color = score >= 70 ? "bg-emerald-500" : score >= 40 ? "bg-blue-500" : "bg-amber-500";
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-slate-600 w-40 truncate">{formatted}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(score, 100)}%` }} />
      </div>
      <span className="text-xs font-medium text-slate-700 w-8 text-right">{score}</span>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}
