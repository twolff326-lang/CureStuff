"use client";

import { useState } from "react";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Types matching the backend response
// ---------------------------------------------------------------

interface ProbeResult {
  source: string;
  display_name: string;
  url: string;
  reachable: boolean;
  has_data: boolean;
  response_time_ms: number;
  sample_count: number | null;
  sample_preview: string;
  error: string;
  status_code: number | null;
  notes: string;
}

interface PreflightResponse {
  summary: {
    total_sources: number;
    reachable: number;
    returning_data: number;
    failed: number;
    all_ok: boolean;
  };
  results: ProbeResult[];
}

// ---------------------------------------------------------------
// Component
// ---------------------------------------------------------------

export default function PreflightPanel() {
  const [running, setRunning] = useState(false);
  const [data, setData] = useState<PreflightResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runChecks = async () => {
    setRunning(true);
    setError(null);
    setData(null);
    try {
      const res = await fetchApi<PreflightResponse>(
        "/api/ingestion/preflight",
        { method: "POST" },
      );
      setData(res);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to run preflight checks",
      );
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mb-8 rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-base font-semibold text-slate-800">
            API Preflight Check
          </h2>
          <p className="mt-0.5 text-xs text-slate-400">
            Probe every external API with a single request to verify
            connectivity and data availability before ingesting.
          </p>
        </div>
        <button
          onClick={runChecks}
          disabled={running}
          className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors ${
            running
              ? "cursor-not-allowed bg-slate-100 text-slate-400"
              : "bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800"
          }`}
        >
          {running && <Spinner />}
          {running ? "Checking..." : data ? "Re-check All APIs" : "Run Preflight Check"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-5 mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Running state */}
      {running && !data && (
        <div className="flex items-center justify-center gap-3 px-5 py-12 text-sm text-slate-400">
          <Spinner />
          Probing 11 external APIs simultaneously...
        </div>
      )}

      {/* Results */}
      {data && (
        <div>
          {/* Summary strip */}
          <div
            className={`flex items-center gap-6 border-b px-5 py-3 ${
              data.summary.all_ok
                ? "border-green-100 bg-green-50"
                : data.summary.failed > 0
                  ? "border-red-100 bg-red-50"
                  : "border-amber-100 bg-amber-50"
            }`}
          >
            <SummaryBadge
              label="All OK"
              ok={data.summary.all_ok}
            />
            <SummaryStat
              label="Reachable"
              value={data.summary.reachable}
              total={data.summary.total_sources}
              color="blue"
            />
            <SummaryStat
              label="Returning Data"
              value={data.summary.returning_data}
              total={data.summary.total_sources}
              color="green"
            />
            {data.summary.failed > 0 && (
              <SummaryStat
                label="Failed"
                value={data.summary.failed}
                total={data.summary.total_sources}
                color="red"
              />
            )}
          </div>

          {/* Results table */}
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                  Source
                </th>
                <th className="px-4 py-2.5 text-center text-xs font-medium uppercase tracking-wide text-slate-500">
                  Status
                </th>
                <th className="px-4 py-2.5 text-right text-xs font-medium uppercase tracking-wide text-slate-500">
                  Latency
                </th>
                <th className="px-4 py-2.5 text-right text-xs font-medium uppercase tracking-wide text-slate-500">
                  Records
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                  Sample
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {data.results.map((r) => (
                <tr
                  key={r.source}
                  className={`transition-colors ${
                    r.has_data
                      ? "hover:bg-green-50/50"
                      : r.error
                        ? "bg-red-50/40 hover:bg-red-50/60"
                        : "hover:bg-slate-50"
                  }`}
                >
                  {/* Source name */}
                  <td className="px-4 py-2.5">
                    <div className="text-sm font-medium text-slate-800">
                      {r.display_name}
                    </div>
                    <div className="text-[11px] text-slate-400">{r.notes}</div>
                  </td>

                  {/* Status badge */}
                  <td className="px-4 py-2.5 text-center">
                    {r.has_data ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-semibold text-green-700">
                        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                        OK
                      </span>
                    ) : r.reachable ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-700">
                        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01" />
                        </svg>
                        No Data
                      </span>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-700"
                        title={r.error}
                      >
                        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                        Fail
                      </span>
                    )}
                  </td>

                  {/* Response time */}
                  <td className="px-4 py-2.5 text-right">
                    <span
                      className={`text-sm tabular-nums ${
                        r.response_time_ms > 5000
                          ? "text-amber-600"
                          : r.response_time_ms > 0
                            ? "text-slate-600"
                            : "text-slate-300"
                      }`}
                    >
                      {r.response_time_ms > 0 ? `${r.response_time_ms}ms` : "\u2014"}
                    </span>
                  </td>

                  {/* Sample count */}
                  <td className="px-4 py-2.5 text-right">
                    <span className="text-sm tabular-nums text-slate-600">
                      {r.sample_count !== null
                        ? r.sample_count.toLocaleString()
                        : "\u2014"}
                    </span>
                  </td>

                  {/* Sample preview */}
                  <td className="max-w-xs truncate px-4 py-2.5 text-xs text-slate-500">
                    {r.error ? (
                      <span className="text-red-500">{r.error}</span>
                    ) : (
                      r.sample_preview || "\u2014"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Small sub-components
// ---------------------------------------------------------------

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

function SummaryBadge({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-2">
      {ok ? (
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-green-500">
          <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
      ) : (
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-red-500">
          <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </div>
      )}
      <span
        className={`text-sm font-semibold ${
          ok ? "text-green-700" : "text-red-700"
        }`}
      >
        {ok ? "All APIs OK" : "Issues Detected"}
      </span>
    </div>
  );
}

function SummaryStat({
  label,
  value,
  total,
  color,
}: {
  label: string;
  value: number;
  total: number;
  color: "blue" | "green" | "red";
}) {
  const textColor =
    color === "blue"
      ? "text-blue-700"
      : color === "green"
        ? "text-green-700"
        : "text-red-700";

  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
        {label}
      </p>
      <p className={`text-sm font-bold tabular-nums ${textColor}`}>
        {value}/{total}
      </p>
    </div>
  );
}
