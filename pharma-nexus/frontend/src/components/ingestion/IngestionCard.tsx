"use client";

import { useEffect, useRef, useState } from "react";

interface IngestionError {
  context: string;
  error: string;
  type: string;
  record_id: string;
  timestamp: string;
}

export interface SourceStatus {
  id: number;
  source: string;
  task_type: string;
  status: string; // "running" | "completed" | "failed"
  records_processed: number;
  total_expected: number | null;
  errors: IngestionError[] | null;
  started_at: string | null;
  completed_at: string | null;
}

interface IngestionCardProps {
  name: string;
  sourceKey: string;
  description: string;
  status: SourceStatus | null;
  onStart: (sourceKey: string) => void;
  starting: boolean;
}

function formatNumber(n: number): string {
  return n.toLocaleString("en-US");
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
  const remMins = mins % 60;
  return `${hrs}h ${remMins}m`;
}

export default function IngestionCard({
  name,
  sourceKey,
  description,
  status,
  onStart,
  starting,
}: IngestionCardProps) {
  const isRunning = status?.status === "running";
  const isCompleted = status?.status === "completed";
  const isFailed = status?.status === "failed";
  const hasStatus = status !== null;

  const [errorsExpanded, setErrorsExpanded] = useState(false);

  // Animate the displayed record count toward the real value
  const [displayCount, setDisplayCount] = useState(0);
  const targetRef = useRef(0);
  const animRef = useRef<number | null>(null);

  useEffect(() => {
    const target = status?.records_processed ?? 0;
    targetRef.current = target;

    // If no animation running and counts differ, start one
    if (animRef.current === null && displayCount !== target) {
      const step = () => {
        setDisplayCount((prev) => {
          const current = targetRef.current;
          if (prev >= current) {
            animRef.current = null;
            return current;
          }
          // Increment by ~2-5% of remaining distance, min 1
          const increment = Math.max(1, Math.ceil((current - prev) * 0.08));
          const next = Math.min(prev + increment, current);
          if (next < current) {
            animRef.current = requestAnimationFrame(step);
          } else {
            animRef.current = null;
          }
          return next;
        });
      };
      animRef.current = requestAnimationFrame(step);
    }

    return () => {
      if (animRef.current !== null) {
        cancelAnimationFrame(animRef.current);
        animRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.records_processed]);

  // Reset display count when source changes status from null to something
  useEffect(() => {
    if (!hasStatus) setDisplayCount(0);
  }, [hasStatus]);

  const totalExpected = status?.total_expected ?? null;
  const progressPct =
    isRunning && totalExpected && totalExpected > 0
      ? Math.min(100, Math.round(((status?.records_processed ?? 0) / totalExpected) * 100))
      : null;
  const errorCount = status?.errors?.length ?? 0;

  return (
    <div
      className={`relative overflow-hidden rounded-lg border bg-white shadow-sm transition-all duration-300 ${
        isRunning
          ? "border-blue-300 ring-2 ring-blue-100"
          : isFailed
            ? "border-red-300"
            : isCompleted
              ? "border-green-300"
              : "border-slate-200"
      }`}
    >
      {/* Progress bar background — fills from left */}
      {isRunning && (
        <div className="absolute inset-0 z-0">
          <div
            className="h-full bg-gradient-to-r from-blue-50 to-blue-100 transition-all duration-700 ease-out"
            style={{ width: "100%" }}
          />
          {/* Animated shimmer sweep */}
          <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/40 to-transparent" />
        </div>
      )}

      {isCompleted && (
        <div className="absolute inset-0 z-0 bg-gradient-to-r from-green-50/60 to-emerald-50/40" />
      )}

      {isFailed && (
        <div className="absolute inset-0 z-0 bg-gradient-to-r from-red-50/60 to-red-50/30" />
      )}

      <div className="relative z-10 p-5">
        {/* Header row */}
        <div className="flex items-start justify-between">
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-slate-800">{name}</h3>
            <p className="mt-1 text-sm text-slate-500">{description}</p>
          </div>
          <StatusBadge status={status?.status ?? null} />
        </div>

        {/* Live stats bar — visible when running or completed */}
        {hasStatus && (
          <div className="mt-4 space-y-2">
            {/* Record count + elapsed */}
            <div className="flex items-end justify-between">
              <div className="flex items-baseline gap-1.5">
                <span
                  className={`text-2xl font-bold tabular-nums tracking-tight ${
                    isRunning ? "text-blue-700" : isCompleted ? "text-green-700" : "text-red-700"
                  }`}
                >
                  {formatNumber(displayCount)}
                </span>
                {totalExpected ? (
                  <span className="text-sm font-medium text-slate-500">
                    of {formatNumber(totalExpected)}
                  </span>
                ) : (
                  <span className="text-sm font-medium text-slate-500">
                    records
                  </span>
                )}
                {progressPct !== null && (
                  <span className="text-xs tabular-nums text-blue-500">
                    ({progressPct}%)
                  </span>
                )}
              </div>
              {status?.started_at && (
                <span className="text-xs tabular-nums text-slate-400">
                  {elapsed(status.started_at, status.completed_at)}
                </span>
              )}
            </div>

            {/* Progress bar */}
            {isRunning && progressPct !== null && (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-blue-100">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all duration-700 ease-out"
                  style={{ width: `${Math.max(2, progressPct)}%` }}
                />
              </div>
            )}
            {isRunning && progressPct === null && (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-blue-100">
                <div className="h-full animate-progress-indeterminate rounded-full bg-blue-500" />
              </div>
            )}

            {isCompleted && (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-green-100">
                <div className="h-full w-full rounded-full bg-green-500 transition-all duration-500" />
              </div>
            )}

            {isFailed && (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-red-100">
                <div className="h-full w-3/4 rounded-full bg-red-400" />
              </div>
            )}

            {/* Error count — clickable to expand details */}
            {errorCount > 0 && (
              <button
                type="button"
                onClick={() => setErrorsExpanded((v) => !v)}
                className="flex w-full items-center gap-1.5 text-left text-xs text-red-600 hover:text-red-800 transition-colors"
              >
                <svg
                  className={`h-3 w-3 shrink-0 transition-transform duration-200 ${errorsExpanded ? "rotate-90" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
                <span className="font-medium">
                  {errorCount} error{errorCount !== 1 ? "s" : ""}
                </span>
                <span className="text-red-400">
                  {errorsExpanded ? "hide" : "show details"}
                </span>
              </button>
            )}

            {/* Expanded error details panel */}
            {errorsExpanded && errorCount > 0 && (
              <ErrorDetailsPanel errors={(status?.errors ?? []) as IngestionError[]} />
            )}
          </div>
        )}

        {/* Action button */}
        {!isRunning && (
          <button
            onClick={() => onStart(sourceKey)}
            disabled={starting}
            className={`mt-4 w-full rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              starting
                ? "cursor-not-allowed bg-slate-100 text-slate-400"
                : "bg-slate-900 text-white hover:bg-slate-800 active:bg-slate-700"
            }`}
          >
            {starting
              ? "Starting..."
              : isCompleted
                ? "Re-ingest"
                : isFailed
                  ? "Retry"
                  : "Start Ingestion"}
          </button>
        )}

        {/* Pulsing dot while running */}
        {isRunning && (
          <div className="mt-4 flex items-center gap-2 text-sm text-blue-600">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-500" />
            </span>
            Ingesting data...
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorDetailsPanel({ errors }: { errors: IngestionError[] }) {
  // Group errors by context for a compact summary
  const grouped = new Map<string, { count: number; type: string; sample: string; record_ids: string[] }>();
  for (const err of errors) {
    const key = err.context || "unknown";
    const existing = grouped.get(key);
    if (existing) {
      existing.count++;
      if (existing.record_ids.length < 3) {
        existing.record_ids.push(err.record_id);
      }
    } else {
      grouped.set(key, {
        count: 1,
        type: err.type || "Error",
        sample: err.error || "No message",
        record_ids: err.record_id ? [err.record_id] : [],
      });
    }
  }

  const MAX_DETAIL_ROWS = 50;
  const showRaw = errors.length <= MAX_DETAIL_ROWS;

  return (
    <div className="mt-2 max-h-64 overflow-y-auto rounded-md border border-red-200 bg-red-50/70 p-3">
      {/* Summary by context */}
      <div className="space-y-2">
        {Array.from(grouped.entries()).map(([ctx, info]) => (
          <div key={ctx} className="rounded border border-red-100 bg-white/80 px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-red-800">{ctx}</span>
              <span className="rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-bold tabular-nums text-red-700">
                {info.count}x
              </span>
            </div>
            <p className="mt-0.5 text-[11px] font-mono text-red-700">
              {info.type}: {info.sample.length > 200 ? info.sample.slice(0, 200) + "..." : info.sample}
            </p>
            {info.record_ids.length > 0 && (
              <p className="mt-0.5 text-[10px] text-red-500">
                records: {info.record_ids.join(", ")}
                {info.count > info.record_ids.length ? ` (+${info.count - info.record_ids.length} more)` : ""}
              </p>
            )}
          </div>
        ))}
      </div>

      {/* Individual error rows for smaller error sets */}
      {showRaw && errors.length > 1 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[10px] font-medium text-red-500 hover:text-red-700">
            Show all {errors.length} individual errors
          </summary>
          <div className="mt-1 space-y-1">
            {errors.map((err, i) => (
              <div key={i} className="rounded border border-red-100 bg-white/60 px-2 py-1 text-[10px]">
                <span className="font-semibold text-red-800">[{err.context}]</span>{" "}
                <span className="font-mono text-red-700">{err.type}: {err.error}</span>
                {err.record_id && (
                  <span className="ml-1 text-red-400">({err.record_id})</span>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string | null }) {
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-700">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-500 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-blue-600" />
        </span>
        Running
      </span>
    );
  }
  if (status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-1 text-xs font-semibold text-green-700">
        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
        Completed
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-1 text-xs font-semibold text-red-700">
        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
        Failed
      </span>
    );
  }
  return (
    <span className="inline-block rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-500">
      Not ingested
    </span>
  );
}
