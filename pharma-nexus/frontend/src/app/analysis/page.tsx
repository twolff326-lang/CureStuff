"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi } from "@/lib/api";
import {
  COST_MODE,
  COST_MODE_ECONOMY,
  COST_MODE_STANDARD,
  COST_MODE_PREMIUM,
  MIN_SCORE_THRESHOLD,
} from "@/lib/glossary";
import InfoTip from "@/components/common/InfoTip";
import type { CancerType, CostInfo, TaskProgress, TaskStatus } from "@/types";

const STORAGE_KEY = "pharma-nexus-jobs";

interface Job {
  taskId: string;
  type: string;
  label: string;
  startedAt: string; // ISO string for serialization
  status: TaskStatus["status"];
  result: Record<string, unknown> | null;
  progress: TaskProgress | null;
}

function isActive(status: string): boolean {
  return status === "PENDING" || status === "STARTED" || status === "PROGRESS";
}

function loadJobs(): Job[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as Job[];
  } catch {
    return [];
  }
}

function saveJobs(jobs: Job[]) {
  try {
    // Keep last 50 jobs
    localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs.slice(0, 50)));
  } catch {
    // localStorage full or unavailable
  }
}

function formatElapsed(startIso: string): string {
  const ms = Date.now() - new Date(startIso).getTime();
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  if (mins < 60) return `${mins}m ${secs}s`;
  const hrs = Math.floor(mins / 60);
  const remainMins = mins % 60;
  return `${hrs}h ${remainMins}m`;
}

export default function AnalysisPage() {
  const [cancerTypes, setCancerTypes] = useState<CancerType[]>([]);
  const [selectedCancer, setSelectedCancer] = useState<string>("");
  const [minScore, setMinScore] = useState(15);
  const [costMode, setCostMode] = useState<string>("economy");
  const [costInfo, setCostInfo] = useState<CostInfo | null>(null);
  const [jobs, setJobs] = useState<Job[]>(loadJobs);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [, setTick] = useState(0); // Force re-render for elapsed time
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load cancer types and cost info
  useEffect(() => {
    fetchApi<{ cancer_types: CancerType[] }>("/api/cancer-types?per_page=200")
      .then((d) => setCancerTypes(d.cancer_types))
      .catch(() => {});
    fetchApi<CostInfo>("/api/hypotheses/analyze/cost-info")
      .then((d) => {
        setCostInfo(d);
        setCostMode(d.current_cost_mode);
      })
      .catch(() => {});
  }, []);

  // Persist jobs to localStorage
  useEffect(() => {
    saveJobs(jobs);
  }, [jobs]);

  // Tick elapsed time every second while jobs are active
  useEffect(() => {
    const hasActiveJobs = jobs.some((j) => isActive(j.status));
    if (hasActiveJobs && !tickRef.current) {
      tickRef.current = setInterval(() => setTick((t) => t + 1), 1000);
    } else if (!hasActiveJobs && tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, [jobs]);

  // Determine correct status endpoint per job type
  function statusUrl(job: Job): string {
    if (job.type === "expression") return `/api/analysis/status/${job.taskId}`;
    return `/api/hypotheses/status/${job.taskId}`;
  }

  // Poll active jobs
  const pollJobs = useCallback(async () => {
    const activeJobs = jobs.filter((j) => isActive(j.status));
    if (activeJobs.length === 0) return;

    const updates = await Promise.allSettled(
      activeJobs.map((j) => fetchApi<TaskStatus>(statusUrl(j)))
    );

    setJobs((prev) =>
      prev.map((job) => {
        const idx = activeJobs.findIndex((aj) => aj.taskId === job.taskId);
        if (idx === -1) return job;
        const res = updates[idx];
        if (res.status === "fulfilled") {
          return {
            ...job,
            status: res.value.status,
            result: res.value.result,
            progress: res.value.progress ?? job.progress,
          };
        }
        return job;
      })
    );
  }, [jobs]);

  useEffect(() => {
    const hasActiveJobs = jobs.some((j) => isActive(j.status));
    if (hasActiveJobs && !pollRef.current) {
      pollRef.current = setInterval(pollJobs, 3000);
    } else if (!hasActiveJobs && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobs, pollJobs]);

  async function submitJob(type: string) {
    setSubmitting(type);
    try {
      let endpoint: string;
      let label: string;
      const body: Record<string, unknown> = { min_score: minScore };

      switch (type) {
        case "cancer":
          endpoint = `/api/hypotheses/generate/cancer/${selectedCancer}`;
          label = `Hypotheses for ${
            cancerTypes.find((c) => String(c.id) === selectedCancer)?.name ??
            selectedCancer
          }`;
          break;
        case "all":
          endpoint = "/api/hypotheses/generate/all";
          label = "All hypotheses (all cancer types)";
          break;
        case "expression":
          endpoint = "/api/analysis/run";
          body.cancer_type_id = selectedCancer
            ? Number(selectedCancer)
            : undefined;
          label = selectedCancer
            ? `Expression analysis for ${
                cancerTypes.find((c) => String(c.id) === selectedCancer)
                  ?.name ?? selectedCancer
              }`
            : "Full expression analysis";
          break;
        case "rescore":
          endpoint = "/api/hypotheses/rescore";
          label = "Rescore all hypotheses";
          break;
        case "confidence":
          endpoint = "/api/hypotheses/analyze/confidence";
          body.min_score = minScore;
          body.limit = 200;
          body.cost_mode = costMode;
          label = `LLM Confidence Assessment (${costMode})`;
          break;
        case "discovery":
          endpoint = "/api/discovery/run";
          body.cost_mode = costMode;
          body.limit = 10;
          if (selectedCancer) {
            body.cancer_type_id = Number(selectedCancer);
          }
          label = `LLM Synthesis Discovery (${costMode})`;
          break;
        case "promote":
          endpoint = "/api/discovery/promote";
          body.min_confidence = 0.5;
          body.limit = 50;
          label = "Promote Proposals → Hypotheses";
          break;
        default:
          return;
      }

      const data = await fetchApi<{ task_id: string }>(endpoint, {
        method: "POST",
        body: JSON.stringify(body),
      });

      setJobs((prev) => [
        {
          taskId: data.task_id,
          type,
          label,
          startedAt: new Date().toISOString(),
          status: "PENDING",
          result: null,
          progress: null,
        },
        ...prev,
      ]);
    } catch (err) {
      alert(
        `Failed to submit: ${
          err instanceof Error ? err.message : "Unknown error"
        }`
      );
    } finally {
      setSubmitting(null);
    }
  }

  function clearCompleted() {
    setJobs((prev) => prev.filter((j) => isActive(j.status)));
  }

  const activeCount = jobs.filter((j) => isActive(j.status)).length;
  const completedCount = jobs.filter((j) => j.status === "SUCCESS").length;
  const failedCount = jobs.filter((j) => j.status === "FAILURE").length;

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Analysis</h1>

      {/* Configuration */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-6">
        <h2 className="text-sm font-medium text-slate-500 mb-4">
          Configuration
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Cancer Type (optional)
            </label>
            <select
              value={selectedCancer}
              onChange={(e) => setSelectedCancer(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">All Cancer Types</option>
              {cancerTypes.map((ct) => (
                <option key={ct.id} value={ct.id}>
                  {ct.name} ({ct.tcga_code})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Minimum Score Threshold<InfoTip text={MIN_SCORE_THRESHOLD} />
            </label>
            <input
              type="number"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              LLM Cost Mode<InfoTip text={COST_MODE} />
            </label>
            <select
              value={costMode}
              onChange={(e) => setCostMode(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="economy">Economy (Haiku) ~$0.01/hyp</option>
              <option value="standard">Standard (Sonnet/Opus) ~$0.04/hyp</option>
              <option value="premium">Premium (Opus) ~$0.19/hyp</option>
            </select>
            {costInfo && (
              <p className="text-xs text-slate-400 mt-1">
                100 hypotheses: ~${costInfo.available_modes[costMode]?.confidence_only_100?.toFixed(2) ?? "?"}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Standard Actions */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <ActionCard
          title="Generate Hypotheses"
          description={
            selectedCancer
              ? "Generate for selected cancer type"
              : "Generate for all cancer types"
          }
          buttonLabel={selectedCancer ? "Generate" : "Generate All"}
          onClick={() => submitJob(selectedCancer ? "cancer" : "all")}
          loading={submitting === "cancer" || submitting === "all"}
          disabled={selectedCancer === "" && submitting === "all"}
        />
        <ActionCard
          title="Expression Analysis"
          description="Run differential expression, drug scoring, and pathway activity"
          buttonLabel="Run Pipeline"
          onClick={() => submitJob("expression")}
          loading={submitting === "expression"}
        />
        <ActionCard
          title="Rescore Hypotheses"
          description="Recalculate composite scores with current weights"
          buttonLabel="Rescore"
          onClick={() => submitJob("rescore")}
          loading={submitting === "rescore"}
        />
        <ActionCard
          title="LLM Confidence"
          description={`Run confidence assessment (${costMode} mode) to activate the feedback loop`}
          buttonLabel="Assess Confidence"
          onClick={() => submitJob("confidence")}
          loading={submitting === "confidence"}
          accent
        />
      </div>

      {/* Novel Discovery Actions */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <ActionCard
          title="LLM Discovery"
          description={`Read papers + drug mechanisms, reason about implicit connections (${costMode})`}
          buttonLabel="Discover Novel Pairs"
          onClick={() => submitJob("discovery")}
          loading={submitting === "discovery"}
          accent
        />
        <ActionCard
          title="Promote Proposals"
          description="Push high-confidence LLM proposals into the hypothesis scoring pipeline"
          buttonLabel="Promote to Hypotheses"
          onClick={() => submitJob("promote")}
          loading={submitting === "promote"}
        />
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 flex flex-col">
          <h3 className="text-sm font-medium text-slate-800 mb-1">
            Quick Stats
          </h3>
          <p className="text-xs text-slate-400 mb-1">
            Active: <span className="font-medium text-blue-700">{activeCount}</span>
          </p>
          <p className="text-xs text-slate-400 mb-1">
            Completed: <span className="font-medium text-emerald-700">{completedCount}</span>
          </p>
          <p className="text-xs text-slate-400 mb-3">
            Failed: <span className="font-medium text-red-700">{failedCount}</span>
          </p>
          {(completedCount > 0 || failedCount > 0) && (
            <button
              onClick={clearCompleted}
              className="text-xs text-slate-500 hover:text-slate-700 underline mt-auto self-start"
            >
              Clear finished
            </button>
          )}
        </div>
      </div>

      {/* Job History */}
      {jobs.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-slate-800 mb-4">
            Job History
          </h2>
          <div className="space-y-3">
            {jobs.map((job) => (
              <JobCard key={job.taskId} job={job} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ActionCard({
  title,
  description,
  buttonLabel,
  onClick,
  loading,
  disabled,
  accent,
}: {
  title: string;
  description: string;
  buttonLabel: string;
  onClick: () => void;
  loading: boolean;
  disabled?: boolean;
  accent?: boolean;
}) {
  return (
    <div className={`rounded-lg shadow-sm border p-5 flex flex-col ${
      accent ? "bg-emerald-50 border-emerald-200" : "bg-white border-slate-200"
    }`}>
      <h3 className="text-sm font-medium text-slate-800 mb-1">{title}</h3>
      <p className="text-xs text-slate-400 mb-4 flex-1">{description}</p>
      <button
        onClick={onClick}
        disabled={loading || disabled}
        className={`w-full px-3 py-2 text-white text-sm rounded-md disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 ${
          accent
            ? "bg-emerald-600 hover:bg-emerald-700"
            : "bg-blue-600 hover:bg-blue-700"
        }`}
      >
        {loading && (
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
        )}
        {buttonLabel}
      </button>
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  PENDING: "bg-amber-100 text-amber-800",
  STARTED: "bg-amber-100 text-amber-800",
  PROGRESS: "bg-blue-100 text-blue-800",
  SUCCESS: "bg-emerald-100 text-emerald-800",
  FAILURE: "bg-red-100 text-red-800",
  RETRY: "bg-orange-100 text-orange-800",
};

function ProgressBar({ progress }: { progress: TaskProgress }) {
  const pct = Math.max(0, Math.min(100, progress.percent));
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-slate-600">
          {progress.step}
        </span>
        <span className="text-xs font-bold text-slate-700">{pct}%</span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
        <div
          className="h-full rounded-full bg-blue-500 transition-all duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-slate-400 mt-1">{progress.detail}</p>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
  const active = isActive(job.status);

  return (
    <div
      className={`bg-white rounded-lg shadow-sm border p-4 ${
        active ? "border-blue-200" : "border-slate-200"
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-800">
              {job.label}
            </span>
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                STATUS_STYLES[job.status] ?? "bg-slate-100 text-slate-600"
              }`}
            >
              {job.status}
            </span>
            {active && (
              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600" />
            )}
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-400 mt-1">
            <span>
              Started: {new Date(job.startedAt).toLocaleTimeString()}
            </span>
            {active && (
              <span className="font-medium text-blue-600">
                Elapsed: {formatElapsed(job.startedAt)}
              </span>
            )}
            <span>ID: {job.taskId.slice(0, 8)}...</span>
          </div>
        </div>
        {job.status === "SUCCESS" && job.result && (
          <div className="text-right">
            {job.result.total_generated !== undefined && (
              <span className="text-sm font-medium text-emerald-700">
                {String(job.result.total_generated)} hypotheses
              </span>
            )}
            {job.result.rescored !== undefined && (
              <span className="text-sm font-medium text-emerald-700">
                {String(job.result.rescored)} rescored
              </span>
            )}
            {job.result.records_processed !== undefined && (
              <span className="text-sm font-medium text-emerald-700">
                {String(job.result.records_processed)} processed
              </span>
            )}
            {job.result.completed !== undefined && job.result.total !== undefined && (
              <span className="text-sm font-medium text-emerald-700">
                {String(job.result.completed)}/{String(job.result.total)} assessed
              </span>
            )}
          </div>
        )}
        {job.status === "FAILURE" && job.result && (
          <div className="text-right max-w-xs">
            <span className="text-xs text-red-600">
              {String(job.result.error ?? "Task failed")}
            </span>
          </div>
        )}
      </div>

      {/* Progress bar for active jobs */}
      {active && job.progress && <ProgressBar progress={job.progress} />}

      {/* Waiting indicator when no progress yet */}
      {active && !job.progress && (
        <div className="mt-2">
          <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
            <div className="h-full w-1/3 rounded-full bg-slate-300 animate-pulse" />
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Waiting for worker to pick up task...
          </p>
        </div>
      )}
    </div>
  );
}
