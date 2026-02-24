"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Types matching the backend /full-report endpoint
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
  elapsed_s?: number;
  traceback: string;
  suggested_fix: string;
  http?: ErrorHTTP;
}

interface Phase {
  name: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_s: number | null;
  records_in: number;
  records_out: number;
  detail: string;
}

interface HttpStats {
  total_requests: number;
  successful: number;
  retries: number;
  failures: number;
  by_status_code: Record<string, number>;
  by_domain: Record<string, { requests: number; total_ms: number }>;
  latency_avg_ms?: number;
  latency_p50_ms?: number;
  latency_p95_ms?: number;
  latency_p99_ms?: number;
  latency_max_ms?: number;
  slowest_call?: { url: string; latency_ms: number; status_code: number };
}

interface DataQuality {
  total_api_records_received: number;
  skipped_non_dict: number;
  skipped_missing_field: number;
  skipped_filtered: number;
  skipped_duplicate: number;
  records_inserted: number;
  records_upserted: number;
}

interface DbOperations {
  batch_upsert_calls: number;
  batch_insert_calls: number;
  total_rows_written: number;
}

interface GuardDecisions {
  stale_logs_expired: number;
  duplicate_check_result: string | null;
  checkpoint_loaded: Record<string, unknown> | null;
}

interface Warning {
  timestamp: string;
  elapsed_s: number;
  context: string;
  message: string;
}

interface TimelineEntry {
  t: string;
  elapsed_s: number;
  event: string;
  detail: string;
}

interface Report {
  phases: Phase[];
  http: HttpStats;
  data_quality: DataQuality;
  db_operations: DbOperations;
  warnings: Warning[];
  timeline: TimelineEntry[];
  guard_decisions: GuardDecisions;
  caches: Record<string, Record<string, number>>;
  summary?: string;
  total_duration_s?: number;
}

interface FullReport {
  source: string;
  log_id: number;
  status: string;
  records_processed: number;
  total_expected: number | null;
  started_at: string | null;
  completed_at: string | null;
  duration: string;
  total_errors: number;
  total_warnings: number;
  report: Report;
  errors: DiagnosticError[];
  plain_text_report: string;
}

// ---------------------------------------------------------------
// Category styling
// ---------------------------------------------------------------

const CATEGORY_STYLES: Record<string, { label: string; bg: string; text: string }> = {
  timeout: { label: "Timeout", bg: "bg-amber-100", text: "text-amber-800" },
  connection: { label: "Connection", bg: "bg-orange-100", text: "text-orange-800" },
  rate_limit: { label: "Rate Limit", bg: "bg-yellow-100", text: "text-yellow-800" },
  api_client_error: { label: "API 4xx", bg: "bg-red-100", text: "text-red-800" },
  api_server_error: { label: "API 5xx", bg: "bg-red-100", text: "text-red-700" },
  api_error: { label: "API Error", bg: "bg-red-100", text: "text-red-700" },
  db_constraint: { label: "DB Constraint", bg: "bg-purple-100", text: "text-purple-800" },
  db_lock: { label: "DB Lock", bg: "bg-purple-100", text: "text-purple-700" },
  data_format: { label: "Data Format", bg: "bg-blue-100", text: "text-blue-800" },
  runtime: { label: "Runtime", bg: "bg-slate-100", text: "text-slate-800" },
  unknown: { label: "Unknown", bg: "bg-slate-100", text: "text-slate-600" },
};

function getCategoryStyle(cat: string) {
  return CATEGORY_STYLES[cat] ?? CATEGORY_STYLES.unknown;
}

type TabKey = "overview" | "performance" | "errors" | "timeline" | "raw";

// ---------------------------------------------------------------
// Component
// ---------------------------------------------------------------

interface Props {
  source: string;
  sourceName: string;
  onClose: () => void;
}

export default function ErrorDiagnosticModal({ source, sourceName, onClose }: Props) {
  const [data, setData] = useState<FullReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [expandedErrors, setExpandedErrors] = useState<Set<number>>(new Set());
  const [activeTab, setActiveTab] = useState<TabKey>("overview");

  const fetchReport = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const result = await fetchApi<FullReport>(
        `/api/ingestion/full-report/${source}`,
      );
      setData(result);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Failed to fetch report");
    } finally {
      setLoading(false);
    }
  }, [source]);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  const handleCopy = async () => {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.plain_text_report);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = data.plain_text_report;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    }
  };

  const toggleError = (idx: number) => {
    setExpandedErrors((prev: Set<number>) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const tabs: { key: TabKey; label: string; count?: number }[] = [
    { key: "overview", label: "Overview" },
    { key: "performance", label: "Performance" },
    {
      key: "errors",
      label: "Errors & Warnings",
      count: (data?.total_errors ?? 0) + (data?.total_warnings ?? 0),
    },
    { key: "timeline", label: "Timeline", count: data?.report?.timeline?.length },
    { key: "raw", label: "Raw Report" },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 backdrop-blur-sm">
      <div className="relative mt-8 mb-8 w-full max-w-5xl rounded-xl border border-slate-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-bold text-slate-900">
              Full Report: {sourceName}
            </h2>
            {data && (
              <p className="mt-0.5 text-sm text-slate-500">
                {data.status} &middot; {data.duration}
                {data.total_errors > 0 && <> &middot; <span className="text-red-600">{data.total_errors} error{data.total_errors !== 1 ? "s" : ""}</span></>}
                {data.total_warnings > 0 && <> &middot; <span className="text-amber-600">{data.total_warnings} warning{data.total_warnings !== 1 ? "s" : ""}</span></>}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopy}
              disabled={!data}
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

        {data && !loading && (
          <>
            {/* Tabs */}
            <div className="flex border-b border-slate-200 px-6 overflow-x-auto">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`flex items-center gap-1.5 whitespace-nowrap px-4 py-2.5 text-sm font-medium transition-colors ${
                    activeTab === tab.key
                      ? "border-b-2 border-blue-500 text-blue-700"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {tab.label}
                  {tab.count != null && tab.count > 0 && (
                    <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                      tab.key === "errors"
                        ? "bg-red-100 text-red-700"
                        : "bg-slate-100 text-slate-600"
                    }`}>
                      {tab.count}
                    </span>
                  )}
                </button>
              ))}
            </div>

            <div className="max-h-[70vh] overflow-y-auto">
              {activeTab === "overview" && <OverviewTab data={data} />}
              {activeTab === "performance" && <PerformanceTab report={data.report} />}
              {activeTab === "errors" && (
                <ErrorsWarningsTab
                  errors={data.errors}
                  warnings={data.report.warnings}
                  expandedErrors={expandedErrors}
                  onToggle={toggleError}
                />
              )}
              {activeTab === "timeline" && <TimelineTab entries={data.report.timeline} />}
              {activeTab === "raw" && <RawTab text={data.plain_text_report} onCopy={handleCopy} copied={copied} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Overview Tab — metadata, phases, guards, data quality
// ---------------------------------------------------------------

function OverviewTab({ data }: { data: FullReport }) {
  const report = data.report;
  const guards = report.guard_decisions;
  const dq = report.data_quality;
  const totalSkipped = dq.skipped_non_dict + dq.skipped_missing_field + dq.skipped_filtered + dq.skipped_duplicate;

  return (
    <div className="p-6 space-y-6">
      {/* Summary */}
      {report.summary && (
        <div className="rounded-lg bg-slate-50 border border-slate-200 px-4 py-3">
          <p className="text-sm text-slate-700">{report.summary}</p>
        </div>
      )}

      {/* Run metadata */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Status" value={data.status} />
        <MetricCard
          label="Records"
          value={`${data.records_processed.toLocaleString()}${data.total_expected ? ` / ${data.total_expected.toLocaleString()}` : ""}`}
        />
        <MetricCard label="Duration" value={data.duration || "N/A"} />
        <MetricCard label="Errors" value={String(data.total_errors)} highlight={data.total_errors > 0 ? "red" : undefined} />
      </div>

      {/* Phases */}
      {report.phases.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Execution Phases</h3>
          <div className="space-y-2">
            {report.phases.map((phase, i) => {
              const statusColor = phase.status === "completed"
                ? "border-green-200 bg-green-50/50"
                : phase.status === "running"
                  ? "border-blue-200 bg-blue-50/50"
                  : "border-red-200 bg-red-50/50";
              const badgeColor = phase.status === "completed"
                ? "bg-green-100 text-green-700"
                : phase.status === "running"
                  ? "bg-blue-100 text-blue-700"
                  : "bg-red-100 text-red-700";
              return (
                <div key={i} className={`rounded-lg border p-3 ${statusColor}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-semibold text-slate-800">{phase.name}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${badgeColor}`}>
                      {phase.status}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-4 text-xs text-slate-600">
                    <div>
                      <span className="text-slate-400">Duration: </span>
                      <span className="font-medium">{phase.duration_s != null ? `${phase.duration_s}s` : "..."}</span>
                    </div>
                    <div>
                      <span className="text-slate-400">In: </span>
                      <span className="font-medium">{phase.records_in.toLocaleString()}</span>
                    </div>
                    <div>
                      <span className="text-slate-400">Out: </span>
                      <span className="font-medium">{phase.records_out.toLocaleString()}</span>
                    </div>
                  </div>
                  {phase.detail && (
                    <p className="mt-1 text-[11px] text-slate-500">{phase.detail}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Guard decisions */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Guard Decisions</h3>
        <div className="rounded-lg border border-slate-200 overflow-hidden">
          <table className="min-w-full text-xs">
            <tbody className="divide-y divide-slate-100">
              <tr>
                <td className="px-3 py-2 font-medium text-slate-500 w-44">Stale logs expired</td>
                <td className="px-3 py-2 text-slate-700">{guards.stale_logs_expired}</td>
              </tr>
              <tr>
                <td className="px-3 py-2 font-medium text-slate-500">Duplicate check</td>
                <td className="px-3 py-2 text-slate-700 font-mono text-[11px]">
                  {guards.duplicate_check_result || "N/A"}
                </td>
              </tr>
              <tr>
                <td className="px-3 py-2 font-medium text-slate-500">Checkpoint loaded</td>
                <td className="px-3 py-2 text-slate-700 font-mono text-[11px]">
                  {guards.checkpoint_loaded
                    ? JSON.stringify(guards.checkpoint_loaded)
                    : "None"}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Data quality */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Data Quality</h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="API Records" value={dq.total_api_records_received.toLocaleString()} />
          <MetricCard
            label="Skipped"
            value={totalSkipped.toLocaleString()}
            highlight={totalSkipped > 0 ? "red" : undefined}
          />
          <MetricCard label="Upserted" value={dq.records_upserted.toLocaleString()} highlight="green" />
          <MetricCard label="Inserted" value={dq.records_inserted.toLocaleString()} highlight="green" />
        </div>
        {totalSkipped > 0 && (
          <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            Skip breakdown: non-dict={dq.skipped_non_dict}, missing field={dq.skipped_missing_field},
            filtered={dq.skipped_filtered}, duplicate={dq.skipped_duplicate}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Performance Tab — HTTP stats, DB ops, caches
// ---------------------------------------------------------------

function PerformanceTab({ report }: { report: Report }) {
  const http = report.http;
  const db = report.db_operations;
  const caches = report.caches;

  return (
    <div className="p-6 space-y-6">
      {/* HTTP overview cards */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-3">HTTP Requests</h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="Total" value={http.total_requests.toLocaleString()} />
          <MetricCard label="Successful" value={http.successful.toLocaleString()} highlight="green" />
          <MetricCard
            label="Retries"
            value={http.retries.toLocaleString()}
            highlight={http.retries > 0 ? "red" : undefined}
          />
          <MetricCard
            label="Failures"
            value={http.failures.toLocaleString()}
            highlight={http.failures > 0 ? "red" : undefined}
          />
        </div>
      </div>

      {/* Latency */}
      {http.latency_avg_ms != null && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Latency</h3>
          <div className="grid grid-cols-5 gap-3">
            <MetricCard label="Avg" value={`${http.latency_avg_ms}ms`} />
            <MetricCard label="p50" value={`${http.latency_p50_ms ?? "N/A"}ms`} />
            <MetricCard label="p95" value={`${http.latency_p95_ms ?? "N/A"}ms`} />
            <MetricCard label="p99" value={`${http.latency_p99_ms ?? "N/A"}ms`} />
            <MetricCard label="Max" value={`${http.latency_max_ms ?? "N/A"}ms`} />
          </div>
          {http.slowest_call && (
            <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <span className="font-semibold">Slowest call:</span>{" "}
              {http.slowest_call.latency_ms}ms ({http.slowest_call.status_code}){" "}
              <span className="font-mono text-[10px]">{http.slowest_call.url}</span>
            </div>
          )}
        </div>
      )}

      {/* Status codes */}
      {Object.keys(http.by_status_code).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Response Status Codes</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(http.by_status_code)
              .sort(([, a], [, b]) => b - a)
              .map(([code, count]) => {
                const c = parseInt(code, 10);
                const color = c >= 200 && c < 300
                  ? "bg-green-100 text-green-700 border-green-200"
                  : c >= 400
                    ? "bg-red-100 text-red-700 border-red-200"
                    : "bg-slate-100 text-slate-700 border-slate-200";
                return (
                  <span key={code} className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${color}`}>
                    {code} <span className="font-normal opacity-70">&times;{count}</span>
                  </span>
                );
              })}
          </div>
        </div>
      )}

      {/* Per-domain */}
      {Object.keys(http.by_domain).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">By Domain</h3>
          <div className="rounded-lg border border-slate-200 overflow-hidden">
            <table className="min-w-full divide-y divide-slate-100 text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-slate-500">Domain</th>
                  <th className="px-3 py-2 text-right font-medium text-slate-500">Requests</th>
                  <th className="px-3 py-2 text-right font-medium text-slate-500">Avg latency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {Object.entries(http.by_domain).map(([domain, stats]) => (
                  <tr key={domain}>
                    <td className="px-3 py-1.5 font-mono text-slate-700">{domain}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{stats.requests}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-500">
                      {stats.requests > 0 ? `${Math.round(stats.total_ms / stats.requests)}ms` : "N/A"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* DB operations */}
      <div>
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Database Operations</h3>
        <div className="grid grid-cols-3 gap-3">
          <MetricCard label="Upsert calls" value={db.batch_upsert_calls.toLocaleString()} />
          <MetricCard label="Insert calls" value={db.batch_insert_calls.toLocaleString()} />
          <MetricCard label="Total rows" value={db.total_rows_written.toLocaleString()} highlight="green" />
        </div>
      </div>

      {/* Caches */}
      {Object.keys(caches).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Caches</h3>
          <div className="space-y-2">
            {Object.entries(caches).map(([name, stats]) => (
              <div key={name} className="rounded-lg border border-slate-200 px-3 py-2">
                <span className="text-xs font-semibold text-slate-700">{name}</span>
                <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-slate-600">
                  {Object.entries(stats).map(([k, v]) => (
                    <span key={k}>
                      <span className="text-slate-400">{k}: </span>
                      <span className="font-medium tabular-nums">{v}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Errors & Warnings Tab
// ---------------------------------------------------------------

function ErrorsWarningsTab({
  errors,
  warnings,
  expandedErrors,
  onToggle,
}: {
  errors: DiagnosticError[];
  warnings: Warning[];
  expandedErrors: Set<number>;
  onToggle: (idx: number) => void;
}) {
  return (
    <div className="p-4 space-y-6">
      {/* Warnings */}
      {warnings.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-amber-700 mb-2">
            Warnings ({warnings.length})
          </h3>
          <div className="space-y-1.5">
            {warnings.map((w, i) => (
              <div key={i} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] tabular-nums text-amber-500">{w.elapsed_s}s</span>
                  <span className="text-xs font-semibold text-amber-800">[{w.context}]</span>
                </div>
                <p className="mt-0.5 text-xs text-amber-700">{w.message}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Errors */}
      {errors.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-red-700 mb-2">
            Errors ({errors.length})
          </h3>
          <div className="space-y-2">
            {errors.map((err, i) => {
              const expanded = expandedErrors.has(i);
              const style = getCategoryStyle(err.category);
              return (
                <div key={i} className="rounded-lg border border-slate-200 overflow-hidden">
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
                        {err.elapsed_s != null && (
                          <span className="text-[10px] tabular-nums text-slate-400">@{err.elapsed_s}s</span>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs font-mono text-red-700 break-all">
                        {err.error.length > 300 ? err.error.slice(0, 300) + "..." : err.error}
                      </p>
                    </div>
                  </button>

                  {expanded && (
                    <div className="border-t border-slate-100 bg-slate-50 px-3 py-3 space-y-2">
                      {err.suggested_fix && (
                        <div className="rounded bg-emerald-50 border border-emerald-200 px-3 py-1.5 text-xs text-emerald-800">
                          <span className="font-semibold">Suggested fix:</span> {err.suggested_fix}
                        </div>
                      )}
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
                      {err.traceback && (
                        <div>
                          <p className="text-[10px] font-semibold text-slate-500 mb-1">Traceback:</p>
                          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-900 px-3 py-2 text-[10px] leading-relaxed text-green-300 font-mono">
                            {err.traceback}
                          </pre>
                        </div>
                      )}
                      <p className="text-[10px] text-slate-400">
                        Recorded at: {err.timestamp}
                      </p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {errors.length === 0 && warnings.length === 0 && (
        <div className="py-8 text-center text-sm text-slate-400">
          No errors or warnings recorded for this run.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Timeline Tab
// ---------------------------------------------------------------

function TimelineTab({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-slate-400">
        No timeline events recorded.
      </div>
    );
  }

  return (
    <div className="p-4">
      <p className="text-xs text-slate-500 mb-3">
        {entries.length} event{entries.length !== 1 ? "s" : ""} recorded during this run.
      </p>
      <div className="relative space-y-0">
        {/* Vertical line */}
        <div className="absolute left-[52px] top-2 bottom-2 w-px bg-slate-200" />

        {entries.map((entry, i) => {
          const isError = entry.event.startsWith("error:");
          const isPhase = entry.event.startsWith("phase_");
          const isGuard = entry.event.includes("guard") || entry.event.includes("stale") || entry.event.includes("checkpoint");
          const dotColor = isError
            ? "bg-red-400"
            : isPhase
              ? "bg-blue-400"
              : isGuard
                ? "bg-amber-400"
                : "bg-slate-300";

          return (
            <div key={i} className="flex items-start gap-3 py-1 group">
              <span className="w-[44px] shrink-0 text-right text-[10px] tabular-nums text-slate-400">
                {entry.elapsed_s}s
              </span>
              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
              <div className="min-w-0 flex-1">
                <span className={`text-xs font-semibold ${
                  isError ? "text-red-700" : isPhase ? "text-blue-700" : "text-slate-700"
                }`}>
                  {entry.event}
                </span>
                {entry.detail && (
                  <span className="ml-2 text-[11px] text-slate-500">{entry.detail}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Raw Tab
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
