"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchApi } from "@/lib/api";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface Phase {
  key: string;
  label: string;
  default_enabled: boolean;
}

interface PhaseStatus {
  key: string;
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

interface PipelineRunStatus {
  id: number;
  status: "running" | "completed" | "failed" | "cancelled";
  current_phase: string | null;
  phases: PhaseStatus[];
  config: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
}

interface PipelineRunSummary {
  id: number;
  status: string;
  current_phase: string | null;
  phases: PhaseStatus[];
  started_at: string | null;
  completed_at: string | null;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function elapsed(start: string | null, end: string | null): string {
  if (!start) return "";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const sec = Math.floor((e - s) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec}s`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin}m`;
}

const statusColors: Record<string, string> = {
  pending: "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  completed:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  skipped:
    "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  cancelled:
    "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
};

/* ------------------------------------------------------------------ */
/* Component                                                           */
/* ------------------------------------------------------------------ */

export default function PipelinePage() {
  /* -- available phases -------------------------------------------- */
  const [availablePhases, setAvailablePhases] = useState<Phase[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  /* -- active run -------------------------------------------------- */
  const [activeRun, setActiveRun] = useState<PipelineRunStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  /* -- history ----------------------------------------------------- */
  const [history, setHistory] = useState<PipelineRunSummary[]>([]);

  /* -- ui state ---------------------------------------------------- */
  const [loading, setLoading] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* -- Load available phases on mount ------------------------------ */
  useEffect(() => {
    fetchApi<{ phases: Phase[] }>("/api/pipeline/phases")
      .then((data) => {
        setAvailablePhases(data.phases);
        setSelected(
          new Set(data.phases.filter((p) => p.default_enabled).map((p) => p.key))
        );
      })
      .catch(() => {
        setError("Failed to load pipeline phases. Is the backend running?");
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  /* -- Load history + check for active run on mount --------------- */
  const loadHistory = useCallback(() => {
    fetchApi<{ runs: PipelineRunSummary[] }>("/api/pipeline/runs")
      .then((data) => {
        setHistory(data.runs);
        const running = data.runs.find((r) => r.status === "running");
        if (running) {
          pollRun(running.id);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    loadHistory();
    return () => {
      mountedRef.current = false;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadHistory]);

  /* -- Poll an active run ----------------------------------------- */
  const pollRun = (runId: number) => {
    if (pollRef.current) clearInterval(pollRef.current);

    const poll = async () => {
      try {
        const data = await fetchApi<PipelineRunStatus>(
          `/api/pipeline/status/${runId}`
        );
        if (!mountedRef.current) return;
        setActiveRun(data);
        if (data.status !== "running") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          loadHistory();
        }
      } catch {
        // ignore transient errors
      }
    };

    poll();
    pollRef.current = setInterval(poll, 2000);
  };

  /* -- Launch pipeline -------------------------------------------- */
  const handleLaunch = async () => {
    if (selected.size === 0) return;
    setLaunching(true);
    setError(null);
    try {
      const resp = await fetchApi<{ run_id: number }>("/api/pipeline/run", {
        method: "POST",
        body: JSON.stringify({ phases: Array.from(selected) }),
      });
      pollRun(resp.run_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start pipeline");
    } finally {
      setLaunching(false);
    }
  };

  /* -- Cancel pipeline ------------------------------------------- */
  const handleCancel = async () => {
    if (!activeRun) return;
    setCancelling(true);
    setError(null);
    try {
      await fetchApi(`/api/pipeline/cancel/${activeRun.id}`, {
        method: "POST",
      });
      // Stop polling immediately so stale callbacks don't overwrite state.
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      // Fetch the final cancelled state.
      try {
        const data = await fetchApi<PipelineRunStatus>(
          `/api/pipeline/status/${activeRun.id}`
        );
        setActiveRun(data);
      } catch {
        // Status fetch failed — mark as cancelled locally so UI updates.
        setActiveRun({ ...activeRun, status: "cancelled" });
      }
      loadHistory();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to cancel pipeline");
    } finally {
      setCancelling(false);
    }
  };

  /* -- Toggle a phase --------------------------------------------- */
  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isRunning = activeRun?.status === "running";

  /* -- Progress summary ------------------------------------------- */
  const completedCount =
    activeRun?.phases.filter((p) => p.status === "completed").length ?? 0;
  const totalPhases = activeRun?.phases.length ?? 0;
  const progressPct = totalPhases > 0 ? (completedCount / totalPhases) * 100 : 0;

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
          Run Full Pipeline
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          One click to ingest data, run analysis, and generate hypotheses.
          Select which phases to include, then launch.
        </p>
      </div>

      {/* Loading state */}
      {loading && !activeRun && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-6 flex items-center justify-center gap-3">
          <svg
            className="animate-spin h-5 w-5 text-blue-600"
            viewBox="0 0 24 24"
            fill="none"
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
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span className="text-sm text-slate-600 dark:text-slate-400">
            Loading pipeline configuration...
          </span>
        </div>
      )}

      {/* Active run progress */}
      {activeRun && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-6 space-y-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
                Pipeline Run #{activeRun.id}
              </h2>
              <span
                className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${
                  statusColors[activeRun.status]
                }`}
              >
                {activeRun.status === "running" && (
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-500 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-600" />
                  </span>
                )}
                {activeRun.status}
              </span>
            </div>
            <div className="flex items-center gap-3">
              {activeRun.started_at && (
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {elapsed(activeRun.started_at, activeRun.completed_at)}
                </span>
              )}
              {activeRun.status === "running" && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 disabled:bg-red-400 text-white text-xs font-semibold transition-colors"
                >
                  {cancelling ? (
                    <>
                      <svg
                        className="animate-spin h-3.5 w-3.5"
                        viewBox="0 0 24 24"
                        fill="none"
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
                          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                        />
                      </svg>
                      Stopping...
                    </>
                  ) : (
                    <>
                      <svg
                        className="w-3.5 h-3.5"
                        fill="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <rect x="6" y="6" width="12" height="12" rx="1" />
                      </svg>
                      Stop Pipeline
                    </>
                  )}
                </button>
              )}
            </div>
          </div>

          {/* Overall progress bar */}
          <div>
            <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400 mb-1">
              <span>
                {completedCount} / {totalPhases} phases complete
              </span>
              <span>{Math.round(progressPct)}%</span>
            </div>
            <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2.5">
              <div
                className={`h-2.5 rounded-full transition-all duration-500 ${
                  activeRun.status === "failed"
                    ? "bg-red-500"
                    : activeRun.status === "completed"
                    ? "bg-emerald-500"
                    : activeRun.status === "cancelled"
                    ? "bg-orange-500"
                    : "bg-blue-500"
                }`}
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>

          {/* Phase list */}
          <div className="space-y-2">
            {activeRun.phases.map((phase, idx) => (
              <div
                key={phase.key}
                className={`flex items-center gap-3 px-4 py-3 rounded-lg border transition-colors ${
                  phase.status === "running"
                    ? "border-blue-300 bg-blue-50 dark:border-blue-700 dark:bg-blue-900/20"
                    : "border-slate-200 dark:border-slate-700"
                }`}
              >
                {/* Step number */}
                <div
                  className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold ${
                    phase.status === "completed"
                      ? "bg-emerald-500 text-white"
                      : phase.status === "running"
                      ? "bg-blue-500 text-white"
                      : phase.status === "failed"
                      ? "bg-red-500 text-white"
                      : phase.status === "cancelled"
                      ? "bg-orange-500 text-white"
                      : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400"
                  }`}
                >
                  {phase.status === "completed" ? (
                    <svg
                      className="w-4 h-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      strokeWidth={2.5}
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="m4.5 12.75 6 6 9-13.5"
                      />
                    </svg>
                  ) : phase.status === "failed" ? (
                    <svg
                      className="w-4 h-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      strokeWidth={2.5}
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M6 18 18 6M6 6l12 12"
                      />
                    </svg>
                  ) : phase.status === "cancelled" ? (
                    <svg
                      className="w-3.5 h-3.5"
                      fill="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <rect x="6" y="6" width="12" height="12" rx="1" />
                    </svg>
                  ) : (
                    idx + 1
                  )}
                </div>

                {/* Label + meta */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-900 dark:text-white">
                      {phase.label}
                    </span>
                    <span
                      className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${
                        statusColors[phase.status]
                      }`}
                    >
                      {phase.status}
                    </span>
                  </div>
                  {phase.error && (
                    <ExpandableError error={phase.error} />
                  )}
                  {phase.result &&
                    phase.result.records_processed !== undefined && (
                      <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                        {String(phase.result.records_processed)} records
                        processed
                      </p>
                    )}
                </div>

                {/* Elapsed time */}
                {phase.started_at && (
                  <span className="text-xs text-slate-500 dark:text-slate-400 tabular-nums">
                    {elapsed(phase.started_at, phase.completed_at)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error banner — always visible so cancel/launch errors aren't hidden */}
      {error && (
        <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
          <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Configuration panel (only when not running and done loading) */}
      {!isRunning && !loading && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-6 space-y-5">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Configure Pipeline
          </h2>

          <div className="space-y-2">
            {availablePhases.map((phase) => (
              <label
                key={phase.key}
                className="flex items-center gap-3 px-4 py-3 rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50 cursor-pointer transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selected.has(phase.key)}
                  onChange={() => toggle(phase.key)}
                  className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm font-medium text-slate-900 dark:text-white">
                  {phase.label}
                </span>
                {!phase.default_enabled && (
                  <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 font-medium">
                    slow
                  </span>
                )}
              </label>
            ))}
          </div>

          <button
            onClick={handleLaunch}
            disabled={launching || selected.size === 0}
            className="w-full py-3 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-slate-400 text-white font-semibold text-sm transition-colors flex items-center justify-center gap-2"
          >
            {launching ? (
              <>
                <svg
                  className="animate-spin h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
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
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                Launching...
              </>
            ) : (
              <>
                <svg
                  className="w-5 h-5"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z"
                  />
                </svg>
                Run Full Pipeline ({selected.size} phases)
              </>
            )}
          </button>
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">
            Previous Runs
          </h2>
          <div className="space-y-2">
            {history.map((run) => {
              const done =
                run.phases?.filter((p) => p.status === "completed").length ?? 0;
              const tot = run.phases?.length ?? 0;
              return (
                <div
                  key={run.id}
                  className="flex items-center justify-between px-4 py-3 rounded-lg border border-slate-200 dark:border-slate-700"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-slate-900 dark:text-white">
                      Run #{run.id}
                    </span>
                    <span
                      className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${
                        statusColors[run.status] ?? statusColors.pending
                      }`}
                    >
                      {run.status}
                    </span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      {done}/{tot} phases
                    </span>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      {elapsed(run.started_at, run.completed_at)}
                    </span>
                    {run.started_at && (
                      <span className="text-xs text-slate-400 dark:text-slate-500">
                        {new Date(run.started_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function ExpandableError({ error }: { error: string }): ReactNode {
  const [expanded, setExpanded] = useState(false);
  const isLong = error.length > 120;

  return (
    <div className="mt-0.5">
      <p
        className={`text-xs text-red-600 dark:text-red-400 ${
          !expanded && isLong ? "truncate" : "whitespace-pre-wrap"
        }`}
      >
        {error}
      </p>
      {isLong && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setExpanded(!expanded);
          }}
          className="text-[11px] font-medium text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 mt-0.5"
        >
          {expanded ? "Show less" : "Show full error"}
        </button>
      )}
    </div>
  );
}
