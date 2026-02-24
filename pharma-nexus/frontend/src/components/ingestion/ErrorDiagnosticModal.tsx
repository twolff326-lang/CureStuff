"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Types matching the backend diagnostic-report endpoint
// ---------------------------------------------------------------

interface ErrorHTTP {
  status_code?: number;
  url?: string;
  method?: string;
  response_body?: string;
}

interface DiagnosticError {
  context: string;
  error: string;
  type: string;
  category: string;
  record_id: string;
  timestamp: string;
  traceback: string;
  suggested_fix: string;
  http?: ErrorHTTP;
}

interface CategorySummary {
  count: number;
  suggested_fix: string;
  sample_error: string;
  sample_type: string;
}

interface ContextSummary {
  count: number;
  category: string;
  sample_error: string;
}

interface DiagnosticReport {
  source: string;
  log_id: number;
  status: string;
  records_processed: number;
  total_expected: number | null;
  started_at: string | null;
  completed_at: string | null;
  duration: string;
  total_errors: number;
  errors_by_category: Record<string, CategorySummary>;
  errors_by_context: Record<string, ContextSummary>;
  errors: DiagnosticError[];
  plain_text_report: string;
}

// ---------------------------------------------------------------
// Category styling
// ---------------------------------------------------------------

const CATEGORY_STYLES: Record<string, { label: string; bg: string; text: string; icon: string }> = {
  timeout: { label: "Timeout", bg: "bg-amber-100", text: "text-amber-800", icon: "clock" },
  connection: { label: "Connection", bg: "bg-orange-100", text: "text-orange-800", icon: "wifi-off" },
  rate_limit: { label: "Rate Limit", bg: "bg-yellow-100", text: "text-yellow-800", icon: "shield" },
  api_client_error: { label: "API 4xx", bg: "bg-red-100", text: "text-red-800", icon: "alert" },
  api_server_error: { label: "API 5xx", bg: "bg-red-100", text: "text-red-700", icon: "server" },
  api_error: { label: "API Error", bg: "bg-red-100", text: "text-red-700", icon: "alert" },
  db_constraint: { label: "DB Constraint", bg: "bg-purple-100", text: "text-purple-800", icon: "database" },
  db_lock: { label: "DB Lock", bg: "bg-purple-100", text: "text-purple-700", icon: "lock" },
  data_format: { label: "Data Format", bg: "bg-blue-100", text: "text-blue-800", icon: "code" },
  runtime: { label: "Runtime", bg: "bg-slate-100", text: "text-slate-800", icon: "bug" },
  unknown: { label: "Unknown", bg: "bg-slate-100", text: "text-slate-600", icon: "?" },
};

function getCategoryStyle(cat: string) {
  return CATEGORY_STYLES[cat] ?? CATEGORY_STYLES.unknown;
}

// ---------------------------------------------------------------
// Component
// ---------------------------------------------------------------

interface Props {
  source: string;
  sourceName: string;
  onClose: () => void;
}

export default function ErrorDiagnosticModal({ source, sourceName, onClose }: Props) {
  const [report, setReport] = useState<DiagnosticReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [expandedErrors, setExpandedErrors] = useState<Set<number>>(new Set());
  const [activeTab, setActiveTab] = useState<"summary" | "details" | "raw">("summary");

  const fetchReport = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const data = await fetchApi<DiagnosticReport>(
        `/api/ingestion/diagnostic-report/${source}`,
      );
      setReport(data);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Failed to fetch report");
    } finally {
      setLoading(false);
    }
  }, [source]);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  const handleCopy = async () => {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(report.plain_text_report);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Fallback for non-HTTPS contexts
      const ta = document.createElement("textarea");
      ta.value = report.plain_text_report;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    }
  };

  const toggleError = (idx: number) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 backdrop-blur-sm">
      <div className="relative mt-8 mb-8 w-full max-w-4xl rounded-xl border border-slate-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-bold text-slate-900">
              Diagnostic Report: {sourceName}
            </h2>
            {report && (
              <p className="mt-0.5 text-sm text-slate-500">
                {report.status} &middot; {report.total_errors} error{report.total_errors !== 1 ? "s" : ""} &middot; {report.duration}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopy}
              disabled={!report}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                copied
                  ? "bg-green-100 text-green-700"
                  : "bg-slate-900 text-white hover:bg-slate-800"
              } disabled:opacity-40`}
            >
              {copied ? "Copied!" : "Copy for Claude Code"}
            </button>
            <button
              onClick={onClose}
              className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Content */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-blue-500" />
          </div>
        )}

        {fetchError && (
          <div className="px-6 py-8 text-center">
            <p className="text-sm text-red-600">{fetchError}</p>
            <button onClick={fetchReport} className="mt-3 text-sm text-blue-600 hover:underline">
              Retry
            </button>
          </div>
        )}

        {report && !loading && (
          <>
            {/* Tabs */}
            <div className="flex border-b border-slate-200 px-6">
              {(["summary", "details", "raw"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2.5 text-sm font-medium transition-colors ${
                    activeTab === tab
                      ? "border-b-2 border-blue-500 text-blue-700"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {tab === "summary" ? "Summary" : tab === "details" ? "All Errors" : "Raw Report"}
                </button>
              ))}
            </div>

            <div className="max-h-[70vh] overflow-y-auto">
              {activeTab === "summary" && (
                <SummaryTab report={report} />
              )}
              {activeTab === "details" && (
                <DetailsTab
                  errors={report.errors}
                  expandedErrors={expandedErrors}
                  onToggle={toggleError}
                />
              )}
              {activeTab === "raw" && (
                <RawTab text={report.plain_text_report} onCopy={handleCopy} copied={copied} />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Summary Tab — category cards + context breakdown
// ---------------------------------------------------------------

function SummaryTab({ report }: { report: DiagnosticReport }) {
  const categories = Object.entries(report.errors_by_category);
  const contexts = Object.entries(report.errors_by_context)
    .sort(([, a], [, b]) => b.count - a.count);

  return (
    <div className="p-6 space-y-6">
      {/* Run metadata */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Status" value={report.status} />
        <MetricCard
          label="Records"
          value={`${report.records_processed.toLocaleString()}${report.total_expected ? ` / ${report.total_expected.toLocaleString()}` : ""}`}
        />
        <MetricCard label="Errors" value={String(report.total_errors)} highlight="red" />
        <MetricCard label="Duration" value={report.duration || "N/A"} />
      </div>

      {/* Error categories */}
      {categories.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Errors by Category</h3>
          <div className="space-y-2">
            {categories.map(([cat, info]) => {
              const style = getCategoryStyle(cat);
              return (
                <div key={cat} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${style.bg} ${style.text}`}>
                        {style.label}
                      </span>
                      <span className="text-sm font-medium text-slate-700">{info.sample_type}</span>
                    </div>
                    <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-bold tabular-nums text-red-700">
                      {info.count}x
                    </span>
                  </div>
                  <p className="text-xs font-mono text-slate-600 truncate">{info.sample_error}</p>
                  <p className="mt-1 text-xs text-emerald-700 bg-emerald-50 rounded px-2 py-1">
                    Fix: {info.suggested_fix}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Error contexts */}
      {contexts.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Errors by Context</h3>
          <div className="rounded-lg border border-slate-200 overflow-hidden">
            <table className="min-w-full divide-y divide-slate-100 text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-slate-500">Context</th>
                  <th className="px-3 py-2 text-center font-medium text-slate-500">Category</th>
                  <th className="px-3 py-2 text-right font-medium text-slate-500">Count</th>
                  <th className="px-3 py-2 text-left font-medium text-slate-500">Sample Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {contexts.map(([ctx, info]) => {
                  const style = getCategoryStyle(info.category);
                  return (
                    <tr key={ctx}>
                      <td className="px-3 py-1.5 font-mono text-slate-700">{ctx}</td>
                      <td className="px-3 py-1.5 text-center">
                        <span className={`inline-block rounded-full px-1.5 py-0.5 text-[9px] font-bold ${style.bg} ${style.text}`}>
                          {style.label}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums font-bold text-red-600">{info.count}</td>
                      <td className="px-3 py-1.5 text-slate-500 truncate max-w-xs">{info.sample_error}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {categories.length === 0 && (
        <div className="py-8 text-center text-sm text-slate-400">
          No errors recorded for this run.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Details Tab — every error with expandable traceback
// ---------------------------------------------------------------

function DetailsTab({
  errors,
  expandedErrors,
  onToggle,
}: {
  errors: DiagnosticError[];
  expandedErrors: Set<number>;
  onToggle: (idx: number) => void;
}) {
  if (errors.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-slate-400">
        No errors recorded.
      </div>
    );
  }

  return (
    <div className="p-4 space-y-2">
      <p className="text-xs text-slate-500 mb-2">
        Showing {errors.length} error{errors.length !== 1 ? "s" : ""}. Click to expand traceback.
      </p>
      {errors.map((err, i) => {
        const expanded = expandedErrors.has(i);
        const style = getCategoryStyle(err.category);
        return (
          <div key={i} className="rounded-lg border border-slate-200 overflow-hidden">
            {/* Clickable header */}
            <button
              type="button"
              onClick={() => onToggle(i)}
              className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-slate-50 transition-colors"
            >
              <svg
                className={`mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`inline-block rounded-full px-1.5 py-0.5 text-[9px] font-bold ${style.bg} ${style.text}`}>
                    {style.label}
                  </span>
                  <span className="text-xs font-semibold text-slate-700">[{err.context}]</span>
                  <span className="text-[10px] text-slate-400">{err.type}</span>
                  {err.record_id && (
                    <span className="text-[10px] font-mono text-slate-400">id:{err.record_id}</span>
                  )}
                </div>
                <p className="mt-0.5 text-xs font-mono text-red-700 break-all">
                  {err.error.length > 300 ? err.error.slice(0, 300) + "..." : err.error}
                </p>
              </div>
            </button>

            {/* Expanded detail */}
            {expanded && (
              <div className="border-t border-slate-100 bg-slate-50 px-3 py-3 space-y-2">
                {/* Suggested fix */}
                {err.suggested_fix && (
                  <div className="rounded bg-emerald-50 border border-emerald-200 px-3 py-1.5 text-xs text-emerald-800">
                    <span className="font-semibold">Suggested fix:</span> {err.suggested_fix}
                  </div>
                )}

                {/* HTTP details */}
                {err.http && (
                  <div className="rounded bg-blue-50 border border-blue-200 px-3 py-2 text-[11px] font-mono text-blue-900 space-y-0.5">
                    <p><span className="font-semibold">URL:</span> {err.http.url}</p>
                    {err.http.status_code && <p><span className="font-semibold">Status:</span> {err.http.status_code}</p>}
                    {err.http.response_body && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-blue-600 hover:text-blue-800 font-medium">
                          Response body ({err.http.response_body.length} chars)
                        </summary>
                        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[10px] text-blue-800 bg-blue-100 rounded p-2">
                          {err.http.response_body}
                        </pre>
                      </details>
                    )}
                  </div>
                )}

                {/* Traceback */}
                {err.traceback && (
                  <div>
                    <p className="text-[10px] font-semibold text-slate-500 mb-1">Traceback:</p>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-900 px-3 py-2 text-[10px] leading-relaxed text-green-300 font-mono">
                      {err.traceback}
                    </pre>
                  </div>
                )}

                {/* Timestamp */}
                <p className="text-[10px] text-slate-400">
                  Recorded at: {err.timestamp}
                </p>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------
// Raw Tab — the plain-text report for copy/paste
// ---------------------------------------------------------------

function RawTab({ text, onCopy, copied }: { text: string; onCopy: () => void; copied: boolean }) {
  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs text-slate-500">
          Pre-formatted report — paste directly into Claude Code for analysis and fixes.
        </p>
        <button
          onClick={onCopy}
          className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
            copied ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
          }`}
        >
          {copied ? "Copied!" : "Copy to clipboard"}
        </button>
      </div>
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 px-4 py-3 text-xs leading-relaxed text-slate-200 font-mono">
        {text}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------

function MetricCard({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: "red" | "green" | "blue";
}) {
  const color =
    highlight === "red"
      ? "text-red-700"
      : highlight === "green"
        ? "text-green-700"
        : highlight === "blue"
          ? "text-blue-700"
          : "text-slate-900";
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className={`text-sm font-bold ${color}`}>{value}</p>
    </div>
  );
}
