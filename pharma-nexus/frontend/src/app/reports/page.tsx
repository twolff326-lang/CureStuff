"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { fetchApi } from "@/lib/api";
import {
  LLM_CONFIDENCE,
  DIMENSION_TOOLTIPS,
  RECOMMENDATION_TOOLTIPS,
  ANALYSIS_PIPELINE,
  ANALYSIS_TYPE_TOOLTIPS,
  DRUG_PORTFOLIO_REPORT,
  COMPARATIVE_REPORT,
  BATCH_REPORTS,
} from "@/lib/glossary";
import InfoTip from "@/components/common/InfoTip";
import type { Hypothesis, CancerType, Drug, ReportFile } from "@/types";

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface AnalysisSummary {
  id: number;
  analysis_type: string;
  model_used: string;
  created_at: string;
}

const ANALYSIS_TYPES = [
  { key: "narrative", label: "Narrative", desc: "Mechanistic explanation of the biology" },
  { key: "critique", label: "Critique", desc: "Peer-review: weaknesses, gaps, risks" },
  { key: "literature_synthesis", label: "Literature", desc: "Synthesis of supporting papers" },
  { key: "experiment_design", label: "Experiments", desc: "Validation plan: in-vitro \u2192 clinical" },
  { key: "confidence", label: "Confidence", desc: "Confidence score with precedent analysis" },
  { key: "comparative", label: "Comparative", desc: "Comparison vs. similar hypotheses" },
];

const REPORT_TYPE_LABELS: Record<string, string> = {
  hypothesis: "Hypothesis",
  cancer_summary: "Cancer Summary",
  drug_portfolio: "Drug Portfolio",
  comparative: "Comparative",
  novel_discoveries: "Novel Discoveries",
  executive_summary: "Executive Summary",
  csv_export: "CSV Export",
  other: "Other",
};

const REPORT_TYPE_COLORS: Record<string, string> = {
  hypothesis: "bg-blue-100 text-blue-800",
  cancer_summary: "bg-violet-100 text-violet-800",
  drug_portfolio: "bg-teal-100 text-teal-800",
  comparative: "bg-orange-100 text-orange-800",
  novel_discoveries: "bg-emerald-100 text-emerald-800",
  executive_summary: "bg-slate-200 text-slate-800",
  csv_export: "bg-amber-100 text-amber-800",
};

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ------------------------------------------------------------------
// Page
// ------------------------------------------------------------------

export default function ReportsPage() {
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [cancers, setCancers] = useState<CancerType[]>([]);
  const [drugs, setDrugs] = useState<Drug[]>([]);
  const [reports, setReports] = useState<ReportFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Hypothesis | null>(null);
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [generating, setGenerating] = useState<Record<string, boolean>>({});
  const [reportGenerating, setReportGenerating] = useState<Record<string, boolean>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});

  // Export filter state
  const [exportMinScore, setExportMinScore] = useState(0);
  const [exportMinNovelty, setExportMinNovelty] = useState(0);
  const [exportCancerId, setExportCancerId] = useState<number | null>(null);

  // Batch generation progress
  const [batchTaskId, setBatchTaskId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<string | null>(null);
  const batchPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Report list filter
  const [reportTypeFilter, setReportTypeFilter] = useState("");

  // Load all data
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [hypRes, cancerRes, drugsRes, reportsRes] = await Promise.allSettled([
        fetchApi<{ hypotheses: Hypothesis[] }>("/api/hypotheses/top?limit=50"),
        fetchApi<{ cancer_types: CancerType[]; total: number }>("/api/cancer-types?per_page=50"),
        fetchApi<{ drugs: Drug[]; total: number }>("/api/drugs?per_page=100"),
        fetchApi<{ reports: ReportFile[]; total: number }>("/api/reports/list"),
      ]);
      if (hypRes.status === "fulfilled") setHypotheses(hypRes.value.hypotheses);
      if (cancerRes.status === "fulfilled") setCancers(cancerRes.value.cancer_types);
      if (drugsRes.status === "fulfilled") setDrugs(drugsRes.value.drugs);
      if (reportsRes.status === "fulfilled") setReports(reportsRes.value.reports);
    } catch { /* degrade */ }
    setLoading(false);
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // Cleanup batch polling on unmount
  useEffect(() => {
    return () => {
      if (batchPollRef.current) clearInterval(batchPollRef.current);
    };
  }, []);

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

  // Download file from response
  const downloadBlob = (blob: Blob, fallbackName: string, res: Response) => {
    const filename = res.headers.get("content-disposition")?.split("filename=")[1]?.replace(/"/g, "")
      ?? fallbackName;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // Generate PDF reports
  const generateReport = async (type: string, entityId?: number) => {
    const rKey = `${type}-${entityId ?? "all"}`;
    setReportGenerating((p) => ({ ...p, [rKey]: true }));
    setMessages((p) => ({ ...p, [rKey]: "" }));
    try {
      let url = "";
      let method = "POST";
      let body: string | undefined;
      const headers: Record<string, string> = {};

      if (type === "hypothesis" && entityId) url = `/api/reports/hypothesis/${entityId}`;
      else if (type === "cancer" && entityId) url = `/api/reports/cancer/${entityId}`;
      else if (type === "drug" && entityId) url = `/api/reports/drug/${entityId}`;
      else if (type === "novel") url = "/api/reports/novel-discoveries";
      else if (type === "executive") url = "/api/reports/executive-summary";
      else if (type === "comparative") {
        url = "/api/reports/comparative";
        headers["Content-Type"] = "application/json";
        body = JSON.stringify({ limit: 20 });
      }

      const res = await fetch(`${API_URL}${url}`, { method, headers, body });
      if (res.ok) {
        const blob = await res.blob();
        downloadBlob(blob, `${type}_report.pdf`, res);
        setMessages((p) => ({ ...p, [rKey]: "Downloaded" }));
        loadData();
      } else {
        const errText = await res.text().catch(() => "");
        setMessages((p) => ({ ...p, [rKey]: `Error: ${res.status}${errText ? ` - ${errText.slice(0, 100)}` : ""}` }));
      }
    } catch {
      setMessages((p) => ({ ...p, [rKey]: "Failed" }));
    } finally {
      setReportGenerating((p) => ({ ...p, [rKey]: false }));
    }
  };

  // Export data (CSV, JSON, Excel)
  const exportData = async (format: "csv" | "json" | "excel", hypothesisId?: number) => {
    const rKey = `export-${format}`;
    setReportGenerating((p) => ({ ...p, [rKey]: true }));
    setMessages((p) => ({ ...p, [rKey]: "" }));
    try {
      let url = "";
      if (format === "csv") {
        const params = new URLSearchParams();
        if (exportMinScore > 0) params.set("min_score", String(exportMinScore));
        if (exportMinNovelty > 0) params.set("min_novelty", String(exportMinNovelty));
        if (exportCancerId) params.set("cancer_type_id", String(exportCancerId));
        url = `${API_URL}/api/reports/export/hypotheses/csv?${params}`;
      } else if (format === "excel") {
        const params = new URLSearchParams();
        if (exportMinScore > 0) params.set("min_score", String(exportMinScore));
        if (exportMinNovelty > 0) params.set("min_novelty", String(exportMinNovelty));
        if (exportCancerId) params.set("cancer_type_id", String(exportCancerId));
        url = `${API_URL}/api/reports/export/hypotheses/excel?${params}`;
      } else if (format === "json" && hypothesisId) {
        url = `${API_URL}/api/reports/export/hypothesis/${hypothesisId}/json`;
      }

      const res = await fetch(url);
      if (res.ok) {
        const blob = await res.blob();
        const ext = format === "excel" ? "xlsx" : format;
        downloadBlob(blob, `pharma_nexus_export.${ext}`, res);
        setMessages((p) => ({ ...p, [rKey]: "Downloaded" }));
        loadData();
      } else {
        setMessages((p) => ({ ...p, [rKey]: `Error: ${res.status}` }));
      }
    } catch {
      setMessages((p) => ({ ...p, [rKey]: "Failed" }));
    } finally {
      setReportGenerating((p) => ({ ...p, [rKey]: false }));
    }
  };

  // Batch generate all reports
  const startBatchGeneration = async () => {
    setReportGenerating((p) => ({ ...p, batch: true }));
    setBatchProgress("Starting...");
    try {
      const res = await fetchApi<{ task_id: string; status: string }>("/api/reports/batch", {
        method: "POST",
      });
      setBatchTaskId(res.task_id);
      setBatchProgress("Queued — polling for progress...");

      // Poll for progress
      batchPollRef.current = setInterval(async () => {
        try {
          const status = await fetchApi<{
            task_state: string;
            progress?: { step: string; detail: string; percent: number };
            task_result?: Record<string, unknown>;
          }>(`/api/ingestion/status/${res.task_id}`);

          if (status.task_state === "PROGRESS" && status.progress) {
            setBatchProgress(`${status.progress.step}: ${status.progress.detail} (${status.progress.percent}%)`);
          } else if (status.task_state === "SUCCESS") {
            setBatchProgress("Complete! All reports generated.");
            setReportGenerating((p) => ({ ...p, batch: false }));
            if (batchPollRef.current) clearInterval(batchPollRef.current);
            loadData();
          } else if (status.task_state === "FAILURE") {
            setBatchProgress("Failed — check server logs.");
            setReportGenerating((p) => ({ ...p, batch: false }));
            if (batchPollRef.current) clearInterval(batchPollRef.current);
          }
        } catch {
          // keep polling
        }
      }, 5000);
    } catch {
      setBatchProgress("Failed to start.");
      setReportGenerating((p) => ({ ...p, batch: false }));
    }
  };

  const hasAnalysis = (type: string) => analyses.some((a) => a.analysis_type === type);
  const analysisCount = selected ? analyses.length : 0;
  const isFullyAnalyzed = analysisCount >= 6;

  // Filter reports
  const filteredReports = reportTypeFilter
    ? reports.filter((r) => r.type === reportTypeFilter)
    : reports;

  const reportTypes = [...new Set(reports.map((r) => r.type))];

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 mb-2">Reports & Export</h1>
      <p className="text-sm text-slate-500 mb-6">
        Generate publication-quality PDFs, side-by-side comparisons, and data exports. Run the analysis pipeline first for comprehensive output.
      </p>

      {/* ── Portfolio Reports ── */}
      <div className="bg-gradient-to-br from-slate-50 to-blue-50 rounded-lg border border-slate-200 p-5 mb-6">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Portfolio Reports</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
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
            title="Comparative Top 20"
            desc="Side-by-side comparison of top hypotheses"
            onClick={() => generateReport("comparative")}
            loading={!!reportGenerating["comparative-all"]}
            msg={messages["comparative-all"]}
            tooltip={COMPARATIVE_REPORT}
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
          {drugs.length > 0 && (
            <div>
              <label className="block text-xs text-slate-500 mb-1">Drug Portfolio<InfoTip text={DRUG_PORTFOLIO_REPORT} /></label>
              <select
                onChange={(e) => { if (e.target.value) generateReport("drug", Number(e.target.value)); }}
                className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm bg-white"
                defaultValue=""
              >
                <option value="" disabled>Select drug...</option>
                {drugs.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      {/* ── Data Export ── */}
      <div className="bg-gradient-to-br from-slate-50 to-emerald-50 rounded-lg border border-slate-200 p-5 mb-6">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Data Export</h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Min Score</label>
            <input
              type="number"
              min={0}
              max={100}
              value={exportMinScore}
              onChange={(e) => setExportMinScore(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm bg-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Min Novelty</label>
            <input
              type="number"
              min={0}
              max={100}
              value={exportMinNovelty}
              onChange={(e) => setExportMinNovelty(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm bg-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Cancer Type (optional)</label>
            <select
              value={exportCancerId ?? ""}
              onChange={(e) => setExportCancerId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm bg-white"
            >
              <option value="">All cancers</option>
              {cancers.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          <div className="flex items-end gap-2">
            <button
              onClick={() => exportData("csv")}
              disabled={!!reportGenerating["export-csv"]}
              className="flex-1 px-3 py-2 bg-white border border-slate-300 text-sm text-slate-700 rounded-md hover:bg-slate-50 disabled:opacity-50"
            >
              {reportGenerating["export-csv"] ? "..." : "CSV"}
            </button>
            <button
              onClick={() => exportData("excel")}
              disabled={!!reportGenerating["export-excel"]}
              className="flex-1 px-3 py-2 bg-white border border-slate-300 text-sm text-slate-700 rounded-md hover:bg-slate-50 disabled:opacity-50"
            >
              {reportGenerating["export-excel"] ? "..." : "Excel"}
            </button>
          </div>
        </div>
        {(messages["export-csv"] || messages["export-excel"]) && (
          <p className="text-xs text-emerald-600">
            {messages["export-csv"] || messages["export-excel"]}
          </p>
        )}
      </div>

      {/* ── Batch Generation ── */}
      <div className="bg-gradient-to-br from-slate-50 to-violet-50 rounded-lg border border-slate-200 p-5 mb-8">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-700">Batch Generate All Reports<InfoTip text={BATCH_REPORTS} /></h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Executive summary + novel discoveries + all cancer summaries + top 50 hypothesis PDFs
            </p>
          </div>
          <button
            onClick={startBatchGeneration}
            disabled={!!reportGenerating.batch}
            className="px-4 py-2 bg-violet-600 text-white text-sm rounded-md hover:bg-violet-700 disabled:opacity-50"
          >
            {reportGenerating.batch ? "Running..." : "Generate All"}
          </button>
        </div>
        {batchProgress && (
          <div className="mt-3">
            <p className="text-xs text-violet-700">{batchProgress}</p>
            {batchTaskId && reportGenerating.batch && (
              <div className="mt-1 h-1.5 rounded-full bg-violet-100 overflow-hidden">
                <div className="h-full rounded-full bg-violet-500 animate-pulse" style={{ width: "60%" }} />
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Hypothesis Analysis + Report Generation ── */}
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
                    <span className="text-xs text-violet-600">{h.llm_confidence_score}% conf<InfoTip text={LLM_CONFIDENCE} /></span>
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
                  <h4 className="text-sm font-semibold text-slate-700">Analysis Pipeline<InfoTip text={ANALYSIS_PIPELINE} /></h4>
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
                          <span className="text-sm font-medium text-slate-800">{at.label}{ANALYSIS_TYPE_TOOLTIPS[at.key] && <InfoTip text={ANALYSIS_TYPE_TOOLTIPS[at.key]} />}</span>
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
                    className="flex-1 min-w-[140px] px-4 py-2.5 bg-slate-900 text-white text-sm rounded-md hover:bg-slate-800 disabled:opacity-50"
                  >
                    {reportGenerating[`hypothesis-${selected.id}`]
                      ? "Generating PDF..."
                      : "PDF Report"}
                  </button>
                  <button
                    onClick={() => exportData("json", selected.id)}
                    disabled={!!reportGenerating["export-json"]}
                    className="px-4 py-2.5 border border-slate-300 text-sm text-slate-700 rounded-md hover:bg-slate-50 disabled:opacity-50"
                  >
                    Export JSON
                  </button>
                  {selected.drug?.id && (
                    <button
                      onClick={() => generateReport("drug", selected.drug!.id)}
                      disabled={!!reportGenerating[`drug-${selected.drug!.id}`]}
                      className="px-4 py-2.5 border border-teal-300 text-sm text-teal-700 rounded-md hover:bg-teal-50 disabled:opacity-50"
                    >
                      {reportGenerating[`drug-${selected.drug!.id}`]
                        ? "Generating..."
                        : "Drug Portfolio"}
                    </button>
                  )}
                </div>
                {messages[`hypothesis-${selected.id}`] && (
                  <p className="text-xs text-emerald-600 mt-2">{messages[`hypothesis-${selected.id}`]}</p>
                )}
              </div>

              {/* Score dimensions */}
              {selected.dimension_scores && (
                <div className="bg-white rounded-lg border border-slate-200 p-5">
                  <h4 className="text-sm font-semibold text-slate-700 mb-3">Evidence Dimensions<InfoTip text="Six independent categories of evidence are scored from 0 to 100 and combined into the composite score." /></h4>
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

      {/* ── Generated Reports Table ── */}
      {reports.length > 0 && (
        <div className="mt-8">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-slate-700">
              Generated Reports ({filteredReports.length}{reportTypeFilter ? ` of ${reports.length}` : ""})
            </h2>
            <div className="flex items-center gap-2">
              <select
                value={reportTypeFilter}
                onChange={(e) => setReportTypeFilter(e.target.value)}
                className="px-2 py-1 text-xs rounded border border-slate-300 bg-white"
              >
                <option value="">All types</option>
                {reportTypes.map((t) => (
                  <option key={t} value={t}>{REPORT_TYPE_LABELS[t] ?? t}</option>
                ))}
              </select>
              <button
                onClick={loadData}
                className="px-2 py-1 text-xs text-blue-600 hover:text-blue-800"
              >
                Refresh
              </button>
            </div>
          </div>
          <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
            <table className="min-w-full divide-y divide-slate-100">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase">File</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase">Type</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase">Format</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Size</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Generated</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {filteredReports.map((r) => (
                  <tr key={r.filename} className="hover:bg-slate-50">
                    <td className="px-4 py-2 font-mono text-xs text-slate-700 max-w-[280px] truncate">{r.filename}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${REPORT_TYPE_COLORS[r.type] ?? "bg-slate-100 text-slate-600"}`}>
                        {REPORT_TYPE_LABELS[r.type] ?? r.type}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500 uppercase">{r.format}</td>
                    <td className="px-4 py-2 text-xs text-slate-500 text-right">{formatBytes(r.size_bytes)}</td>
                    <td className="px-4 py-2 text-xs text-slate-400 text-right">{formatDate(r.generated_at)}</td>
                    <td className="px-4 py-2 text-right">
                      <a
                        href={`${API_URL}${r.download_url}`}
                        className="text-xs text-blue-600 hover:text-blue-800 font-medium"
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
  title, desc, onClick, loading, msg, accent, tooltip,
}: {
  title: string; desc: string; onClick: () => void; loading: boolean; msg?: string; accent?: boolean; tooltip?: string;
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
      <p className="text-sm font-medium text-slate-800">{title}{tooltip && <InfoTip text={tooltip} />}</p>
      <p className="text-xs text-slate-500 mt-0.5">{desc}</p>
      {loading && <p className="text-xs text-blue-600 mt-1 animate-pulse">Generating...</p>}
      {msg && !loading && <p className={`text-xs mt-1 ${msg.startsWith("Error") ? "text-red-500" : "text-emerald-600"}`}>{msg}</p>}
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
      {RECOMMENDATION_TOOLTIPS[rec] && <InfoTip text={RECOMMENDATION_TOOLTIPS[rec]} />}
    </span>
  );
}

function DimensionBar({ label, score }: { label: string; score: number }) {
  const formatted = label.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const color = score >= 70 ? "bg-emerald-500" : score >= 40 ? "bg-blue-500" : "bg-amber-500";
  const dimTip = DIMENSION_TOOLTIPS[label] ?? "";
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-slate-600 w-40 truncate">{formatted}{dimTip && <InfoTip text={dimTip} />}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(score, 100)}%` }} />
      </div>
      <span className="text-xs font-medium text-slate-700 w-8 text-right">{score}</span>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
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
