"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi } from "@/lib/api";

const ErrorDiagnosticModal = dynamic(
  () => import("@/components/ingestion/ErrorDiagnosticModal"),
  { ssr: false },
);

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

interface IngestionError {
  context: string;
  error: string;
  type: string;
  category?: string;
  record_id?: string;
  timestamp?: string;
  traceback?: string;
  suggested_fix?: string;
  http?: {
    status_code?: number;
    url?: string;
    response_body?: string;
  };
}

interface SourceStatus {
  id: number;
  source: string;
  task_type: string;
  status: string;
  records_processed: number;
  total_expected: number | null;
  errors: IngestionError[] | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds?: number | null;
}

interface LiveStatusResponse {
  sources: Record<string, SourceStatus>;
}

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

const SOURCE_NAMES: Record<string, string> = {
  drugbank: "DrugBank",
  pubchem: "PubChem",
  chembl: "ChEMBL",
  cbioportal: "cBioPortal",
  tcga: "TCGA / GDC",
  cosmic: "COSMIC",
  kegg: "KEGG",
  reactome: "Reactome",
  string: "STRING",
  uniprot: "UniProt",
  opentargets: "OpenTargets",
  literature: "PubMed",
  clinical_trials: "ClinicalTrials.gov",
  prism: "PRISM / GDSC",
  depmap: "DepMap",
};

function friendlyName(source: string): string {
  return SOURCE_NAMES[source] || source;
}

function elapsed(startedAt: string, completedAt: string | null): string {
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const secs = Math.floor((end - start) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  if (mins < 60) return `${mins}m ${remSecs}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

const CATEGORY_COLORS: Record<string, string> = {
  timeout: "bg-amber-100 text-amber-800",
  connection: "bg-orange-100 text-orange-800",
  rate_limit: "bg-yellow-100 text-yellow-800",
  api_client_error: "bg-red-100 text-red-800",
  api_server_error: "bg-red-100 text-red-700",
  api_error: "bg-red-100 text-red-700",
  db_constraint: "bg-purple-100 text-purple-800",
  db_lock: "bg-purple-100 text-purple-700",
  data_format: "bg-blue-100 text-blue-800",
  runtime: "bg-slate-200 text-slate-800",
  unknown: "bg-slate-100 text-slate-600",
};

// ---------------------------------------------------------------
// Page
// ---------------------------------------------------------------

export default function DiagnosticsPage() {
  const [statuses, setStatuses] = useState<Record<string, SourceStatus>>({});
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "errors" | "failed" | "running">("all");
  const mountedRef = useRef(true);

  const anyRunning = Object.values(statuses).some((s) => s.status === "running");

  const fetchStatus = useCallback(async () => {
    try {
      const data = await fetchApi<LiveStatusResponse>("/api/ingestion/live-status");
      if (mountedRef.current) setStatuses(data.sources);
    } catch {
      // retry next interval
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchStatus();
    return () => { mountedRef.current = false; };
  }, [fetchStatus]);

  useEffect(() => {
    const ms = anyRunning ? 2000 : 15000;
    const id = setInterval(fetchStatus, ms);
    return () => clearInterval(id);
  }, [anyRunning, fetchStatus]);

  // Derive source list sorted by severity
  const sourceList = Object.values(statuses).sort((a, b) => {
    const errA = a.errors?.length ?? 0;
    const errB = b.errors?.length ?? 0;
    // Failed first, then by error count descending
    if (a.status === "failed" && b.status !== "failed") return -1;
    if (b.status === "failed" && a.status !== "failed") return 1;
    return errB - errA;
  });

  const filtered = sourceList.filter((s) => {
    if (filter === "errors") return (s.errors?.length ?? 0) > 0;
    if (filter === "failed") return s.status === "failed";
    if (filter === "running") return s.status === "running";
    return true;
  });

  // Aggregate stats
  const totalErrors = sourceList.reduce((sum, s) => sum + (s.errors?.length ?? 0), 0);
  const failedCount = sourceList.filter((s) => s.status === "failed").length;
  const runningCount = sourceList.filter((s) => s.status === "running").length;
  const healthyCount = sourceList.filter(
    (s) => s.status === "completed" && (s.errors?.length ?? 0) === 0,
  ).length;

  // Category breakdown across all sources
  const allCategories = new Map<string, number>();
  for (const s of sourceList) {
    for (const err of s.errors ?? []) {
      const cat = (err as IngestionError).category || "unknown";
      allCategories.set(cat, (allCategories.get(cat) ?? 0) + 1);
    }
  }

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-2">Ingestion Diagnostics</h1>
      <p className="text-slate-500 mb-6">
        Monitor errors across all data sources. Click any source to view full diagnostic report with tracebacks.
      </p>

      {/* Aggregate stats strip */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Total Errors" value={totalErrors} highlight={totalErrors > 0 ? "red" : undefined} />
        <StatCard label="Failed Sources" value={failedCount} highlight={failedCount > 0 ? "red" : undefined} />
        <StatCard label="Running" value={runningCount} highlight={runningCount > 0 ? "blue" : undefined} />
        <StatCard label="Healthy" value={healthyCount} highlight={healthyCount > 0 ? "green" : undefined} />
      </div>

      {/* Error category breakdown */}
      {allCategories.size > 0 && (
        <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700 mb-3">Error Categories (all sources)</h2>
          <div className="flex flex-wrap gap-2">
            {Array.from(allCategories.entries())
              .sort(([, a], [, b]) => b - a)
              .map(([cat, count]) => (
                <span
                  key={cat}
                  className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold ${
                    CATEGORY_COLORS[cat] ?? CATEGORY_COLORS.unknown
                  }`}
                >
                  {cat}
                  <span className="rounded-full bg-white/60 px-1.5 py-0.5 text-[10px]">{count}</span>
                </span>
              ))}
          </div>
        </div>
      )}

      {/* Filter tabs */}
      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1 w-fit">
        {([
          ["all", `All (${sourceList.length})`],
          ["errors", `With Errors (${sourceList.filter((s) => (s.errors?.length ?? 0) > 0).length})`],
          ["failed", `Failed (${failedCount})`],
          ["running", `Running (${runningCount})`],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              filter === key
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Source rows */}
      <div className="space-y-2">
        {filtered.length === 0 && (
          <div className="rounded-lg border border-slate-200 bg-white py-12 text-center text-sm text-slate-400">
            {filter === "all"
              ? "No ingestion runs yet."
              : `No sources matching "${filter}" filter.`}
          </div>
        )}

        {filtered.map((s) => (
          <SourceRow
            key={s.source}
            status={s}
            onClick={() => setSelectedSource(s.source)}
          />
        ))}
      </div>

      {/* Diagnostic modal */}
      {selectedSource && (
        <ErrorDiagnosticModal
          source={selectedSource}
          sourceName={friendlyName(selectedSource)}
          onClose={() => setSelectedSource(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Source Row
// ---------------------------------------------------------------

function SourceRow({ status, onClick }: { status: SourceStatus; onClick: () => void }) {
  const errorCount = status.errors?.length ?? 0;
  const isFailed = status.status === "failed";
  const isRunning = status.status === "running";
  const isCompleted = status.status === "completed";

  // Collect category breakdown for this source
  const categories = new Map<string, number>();
  for (const err of status.errors ?? []) {
    const cat = (err as IngestionError).category || "unknown";
    categories.set(cat, (categories.get(cat) ?? 0) + 1);
  }

  // Top error message (first error)
  const topError = status.errors?.[0] as IngestionError | undefined;

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left rounded-lg border bg-white shadow-sm transition-all hover:shadow-md hover:border-slate-300 ${
        isFailed
          ? "border-red-200 bg-red-50/30"
          : errorCount > 0
            ? "border-amber-200 bg-amber-50/20"
            : isRunning
              ? "border-blue-200"
              : "border-slate-200"
      }`}
    >
      <div className="px-4 py-3 sm:px-5">
        {/* Top line: source name, status, error count, duration */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <h3 className="text-sm font-semibold text-slate-800 truncate">
              {friendlyName(status.source)}
            </h3>
            <StatusPill status={status.status} />
            {isRunning && (
              <span className="text-xs tabular-nums text-blue-600">
                {status.records_processed.toLocaleString()}
                {status.total_expected ? ` / ${status.total_expected.toLocaleString()}` : " records"}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {errorCount > 0 && (
              <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-bold tabular-nums text-red-700">
                {errorCount} error{errorCount !== 1 ? "s" : ""}
              </span>
            )}
            {(isCompleted || isFailed) && (
              <span className="text-xs tabular-nums text-slate-400">
                {status.records_processed.toLocaleString()} records
              </span>
            )}
            {status.started_at && (
              <span className="text-xs tabular-nums text-slate-400">
                {elapsed(status.started_at, status.completed_at)}
              </span>
            )}
            <svg className="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </div>
        </div>

        {/* Error detail line — only when errors exist */}
        {errorCount > 0 && (
          <div className="mt-2 space-y-1.5">
            {/* Category pills */}
            <div className="flex flex-wrap gap-1">
              {Array.from(categories.entries()).map(([cat, count]) => (
                <span
                  key={cat}
                  className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    CATEGORY_COLORS[cat] ?? CATEGORY_COLORS.unknown
                  }`}
                >
                  {cat} ({count})
                </span>
              ))}
            </div>

            {/* First error preview */}
            {topError && (
              <p className="text-xs font-mono text-red-700 truncate">
                [{topError.context}] {topError.type}: {topError.error}
              </p>
            )}

            {/* Suggested fix from first error */}
            {topError?.suggested_fix && (
              <p className="text-[11px] text-emerald-700 bg-emerald-50 rounded px-2 py-0.5 truncate">
                Fix: {topError.suggested_fix}
              </p>
            )}
          </div>
        )}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------
// Small components
// ---------------------------------------------------------------

function StatusPill({ status }: { status: string }) {
  const styles: Record<string, string> = {
    completed: "bg-green-100 text-green-700",
    running: "bg-blue-100 text-blue-700",
    failed: "bg-red-100 text-red-700",
  };
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${styles[status] ?? "bg-slate-100 text-slate-600"}`}>
      {status}
    </span>
  );
}

function StatCard({
  label,
  value,
  highlight,
}: {
  label: string;
  value: number;
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
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>
    </div>
  );
}
