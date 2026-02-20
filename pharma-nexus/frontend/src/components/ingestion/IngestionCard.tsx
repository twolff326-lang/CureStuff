"use client";

import { useEffect, useRef, useState } from "react";

export interface SourceStatus {
  id: number;
  source: string;
  task_type: string;
  status: string; // "running" | "completed" | "failed"
  records_processed: number;
  total_expected: number | null;
  errors: unknown[] | null;
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

            {/* Error count */}
            {errorCount > 0 && (
              <p className="text-xs text-red-500">
                {errorCount} error{errorCount !== 1 ? "s" : ""}
              </p>
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
