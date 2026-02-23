"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import IngestionCard, {
  type SourceStatus,
} from "@/components/ingestion/IngestionCard";
import PreflightPanel from "@/components/ingestion/PreflightPanel";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Source definitions — grouped by category
// ---------------------------------------------------------------

interface SourceDef {
  name: string;
  sourceKey: string;
  description: string;
  category: "drugs" | "cancer" | "pathways" | "literature" | "analysis";
}

const sources: SourceDef[] = [
  // Drugs
  { name: "DrugBank", sourceKey: "drugbank", description: "FDA-approved drug data, mechanisms, targets", category: "drugs" },
  { name: "PubChem", sourceKey: "pubchem", description: "Chemical properties, bioassay results", category: "drugs" },
  { name: "ChEMBL", sourceKey: "chembl", description: "Bioactivity data, binding affinities", category: "drugs" },
  // Cancer
  { name: "cBioPortal", sourceKey: "cbioportal", description: "TCGA cancer genomics — mutations, expression, CNA", category: "cancer" },
  { name: "TCGA / GDC", sourceKey: "tcga", description: "Supplementary molecular profiles of 11,000+ tumors", category: "cancer" },
  { name: "COSMIC", sourceKey: "cosmic", description: "Somatic mutations in cancer", category: "cancer" },
  // Pathways
  { name: "KEGG", sourceKey: "kegg", description: "Biological pathway maps", category: "pathways" },
  { name: "Reactome", sourceKey: "reactome", description: "Pathway database with molecular details", category: "pathways" },
  { name: "STRING", sourceKey: "string", description: "Protein-protein interaction networks", category: "pathways" },
  { name: "UniProt", sourceKey: "uniprot", description: "Protein function and interaction data", category: "pathways" },
  { name: "OpenTargets", sourceKey: "opentargets", description: "Target-disease association evidence", category: "pathways" },
  // Literature
  { name: "PubMed", sourceKey: "literature", description: "Published biomedical literature", category: "literature" },
  { name: "ClinicalTrials.gov", sourceKey: "clinical_trials", description: "Active and completed trial data", category: "literature" },
  // Drug sensitivity screens
  { name: "PRISM / GDSC", sourceKey: "prism", description: "Drug sensitivity screens — cell line viability (Broad/Sanger)", category: "drugs" },
  // Computed pipelines
  { name: "Expression Analysis", sourceKey: "full_expression_analysis", description: "Differential expression, pathway activity, drug scores", category: "analysis" },
  { name: "Knowledge Graph", sourceKey: "knowledge_graph", description: "Sync all data to Neo4j for graph queries", category: "analysis" },
];

const CATEGORY_LABELS: Record<string, string> = {
  drugs: "Drug Databases",
  cancer: "Cancer Genomics",
  pathways: "Pathways & Interactions",
  literature: "Literature & Trials",
  analysis: "Computed Pipelines",
};

const CATEGORY_ORDER = ["drugs", "cancer", "pathways", "literature", "analysis"];

const BATCH_ACTIONS: { label: string; sourceKey: string }[] = [
  { label: "All Drugs", sourceKey: "all_drugs" },
  { label: "All Cancer Data", sourceKey: "all_cancer_data" },
  { label: "All Pathways", sourceKey: "all_pathways" },
  { label: "All Literature", sourceKey: "all_literature" },
];

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

interface LiveStatusResponse {
  sources: Record<string, SourceStatus>;
}

interface LogEntry {
  id: number;
  source: string;
  task_type: string;
  status: string;
  records_processed: number;
  total_expected: number | null;
  errors: unknown[] | null;
  started_at: string | null;
  completed_at: string | null;
  data_source_version?: string | null;
  data_downloaded_at?: string | null;
  records_filtered?: number | null;
  duration_seconds?: number | null;
}

// ---------------------------------------------------------------
// Page component
// ---------------------------------------------------------------

export default function DataSourcesPage() {
  const [statuses, setStatuses] = useState<Record<string, SourceStatus>>({});
  const [startingSource, setStartingSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recentLogs, setRecentLogs] = useState<LogEntry[]>([]);
  const mountedRef = useRef(true);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState<string | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [resetDetails, setResetDetails] = useState<Record<string, any> | null>(null);

  // Track whether any source is currently running so we can poll faster
  const anyRunning = Object.values(statuses).some(
    (s) => s.status === "running",
  );

  // ---- Polling ------------------------------------------------
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await fetchApi<LiveStatusResponse>(
        "/api/ingestion/live-status",
      );
      if (mountedRef.current) setStatuses(data.sources);
    } catch {
      // Silently ignore — will retry next interval
    }
  }, []);

  const fetchLogs = useCallback(async () => {
    try {
      const data = await fetchApi<{ logs: LogEntry[] }>(
        "/api/ingestion/logs?per_page=10",
      );
      if (mountedRef.current) setRecentLogs(data.logs);
    } catch {
      // ignore
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchStatus();
    fetchLogs();
  }, [fetchStatus, fetchLogs]);

  // Poll: 1.5 s while running, 10 s when idle
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    const ms = anyRunning ? 1500 : 10000;
    intervalRef.current = setInterval(() => {
      fetchStatus();
      fetchLogs();
    }, ms);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [anyRunning, fetchStatus, fetchLogs]);

  // ---- Start ingestion ----------------------------------------
  const handleStart = async (sourceKey: string) => {
    setStartingSource(sourceKey);
    setError(null);
    try {
      await fetchApi("/api/ingestion/start", {
        method: "POST",
        body: JSON.stringify({ source: sourceKey }),
      });
      // Immediately refetch to pick up the new "running" status
      await fetchStatus();
      await fetchLogs();
    } catch (err) {
      setError(
        `Failed to start ingestion for "${sourceKey}". Is the backend running?`
      );
    } finally {
      setStartingSource(null);
    }
  };

  // ---- Kill switch: full reset ----------------------------------
  const handleResetAll = async () => {
    setResetting(true);
    setError(null);
    setResetResult(null);
    setResetDetails(null);
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const data = await fetchApi<{ status: string; details: Record<string, any> }>(
        "/api/ingestion/reset-all",
        { method: "POST" },
      );
      setResetDetails(data.details);

      if (data.status === "reset_complete") {
        setResetResult("All data wiped successfully.");
      } else if (data.status === "reset_partial") {
        setResetResult("Reset partially succeeded — some subsystems reported errors. See details below.");
      } else if (data.status === "reset_failed") {
        setError("Database wipe FAILED — tables were not cleared. See details below.");
      } else {
        setResetResult(`Reset returned status: ${data.status}`);
      }

      setResetConfirmOpen(false);
      // Refresh UI to reflect new state
      await fetchStatus();
      await fetchLogs();
    } catch (err) {
      setError("Reset failed. Is the backend running?");
    } finally {
      setResetting(false);
    }
  };

  // ---- Summary bar --------------------------------------------
  const totalRecords = Object.values(statuses).reduce(
    (sum, s) => sum + (s.records_processed ?? 0),
    0,
  );
  const runningCount = Object.values(statuses).filter(
    (s) => s.status === "running",
  ).length;
  const completedCount = Object.values(statuses).filter(
    (s) => s.status === "completed",
  ).length;

  // Group sources by category
  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    label: CATEGORY_LABELS[cat],
    sources: sources.filter((s) => s.category === cat),
  }));

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-2">Data Sources</h1>
      <p className="text-slate-500 mb-6">
        Manage data ingestion from public biomedical databases.
      </p>

      {/* Error banner */}
      {error && (
        <div className="mb-6 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3">
          <svg className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <span className="flex-1 text-sm text-red-700">{error}</span>
          <button
            onClick={() => setError(null)}
            className="text-sm font-medium text-red-500 hover:text-red-700"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Aggregate status strip */}
      <div className="mb-6 flex flex-wrap items-center gap-4 rounded-lg border border-slate-200 bg-white px-5 py-3 shadow-sm">
        <Stat label="Total Records" value={totalRecords.toLocaleString()} />
        <Divider />
        <Stat
          label="Running"
          value={String(runningCount)}
          highlight={runningCount > 0 ? "blue" : undefined}
        />
        <Divider />
        <Stat
          label="Completed"
          value={String(completedCount)}
          highlight={completedCount > 0 ? "green" : undefined}
        />
        <Divider />
        <Stat label="Sources" value={String(sources.length)} />
      </div>

      {/* Preflight API connectivity check */}
      <PreflightPanel />

      {/* Source cards by category */}
      {grouped.map((group) => (
        <div key={group.category} className="mb-8">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
            {group.label}
          </h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {group.sources.map((source) => (
              <IngestionCard
                key={source.sourceKey}
                name={source.name}
                sourceKey={source.sourceKey}
                description={source.description}
                status={statuses[source.sourceKey] ?? null}
                onStart={handleStart}
                starting={startingSource === source.sourceKey}
              />
            ))}
          </div>
        </div>
      ))}

      {/* Batch ingestion actions */}
      <div className="mb-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-medium text-slate-500 mb-2">Batch Ingestion</h2>
        <p className="text-xs text-slate-400 mb-3">
          Run entire categories at once. Each source runs as a separate async Celery task.
        </p>
        <div className="flex flex-wrap gap-3">
          {BATCH_ACTIONS.map((action) => (
            <button
              key={action.sourceKey}
              onClick={() => handleStart(action.sourceKey)}
              disabled={startingSource === action.sourceKey}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-800 active:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {startingSource === action.sourceKey
                ? `${action.label}...`
                : action.label}
            </button>
          ))}
        </div>
      </div>

      {/* Recent ingestion activity table */}
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-sm font-medium text-slate-500">
            Recent Ingestion Activity
          </h2>
          <button
            onClick={() => { fetchStatus(); fetchLogs(); }}
            className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-200"
          >
            Refresh
          </button>
        </div>
        {recentLogs.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-slate-400">
            No ingestion runs yet. Click a source above to start.
          </div>
        ) : (
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                  Source
                </th>
                <th className="px-4 py-2 text-center text-xs font-medium uppercase tracking-wide text-slate-500">
                  Status
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wide text-slate-500">
                  Records
                </th>
                <th className="px-4 py-2 text-center text-xs font-medium uppercase tracking-wide text-slate-500">
                  Version
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wide text-slate-500">
                  Time
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {recentLogs.map((log) => (
                <tr key={log.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-2 text-sm text-slate-700">
                    {log.source}
                  </td>
                  <td className="px-4 py-2 text-center">
                    <LogStatusBadge status={log.status} />
                  </td>
                  <td className="px-4 py-2 text-right text-sm tabular-nums text-slate-600">
                    {log.records_processed.toLocaleString()}
                    {log.total_expected ? (
                      <span className="text-slate-400"> / {log.total_expected.toLocaleString()}</span>
                    ) : null}
                    {log.records_filtered ? (
                      <span className="text-amber-500 text-xs ml-1">(-{log.records_filtered.toLocaleString()})</span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-center">
                    {log.data_source_version ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-blue-50 text-blue-700">
                        {log.data_source_version}
                      </span>
                    ) : (
                      <span className="text-slate-300 text-xs">&mdash;</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right text-xs text-slate-400">
                    {log.started_at ? formatTimeAgo(log.started_at) : "\u2014"}
                    {log.duration_seconds ? (
                      <span className="block text-[10px] text-slate-300">
                        {log.duration_seconds < 60
                          ? `${log.duration_seconds.toFixed(1)}s`
                          : `${Math.floor(log.duration_seconds / 60)}m ${Math.round(log.duration_seconds % 60)}s`}
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Kill Switch — danger zone */}
      <div className="mt-10 rounded-lg border-2 border-red-300 bg-red-50 p-5">
        <div className="flex items-center gap-3 mb-2">
          <svg className="h-5 w-5 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <h2 className="text-sm font-semibold text-red-700 uppercase tracking-wider">
            Danger Zone
          </h2>
        </div>
        <p className="text-sm text-red-600 mb-4">
          Wipe <strong>all</strong> ingested data, cancel running tasks, flush
          caches, and clear the knowledge graph. The app returns to a
          clean-slate state. This cannot be undone.
        </p>

        {resetResult && (
          <div className="mb-3 rounded-md bg-green-50 border border-green-200 px-4 py-2 text-sm text-green-700">
            {resetResult}
          </div>
        )}

        {/* Diagnostic details panel — shows full API response */}
        {resetDetails && (
          <ResetDetailsPanel details={resetDetails} />
        )}

        {!resetConfirmOpen ? (
          <button
            onClick={() => setResetConfirmOpen(true)}
            className="rounded-md bg-red-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 active:bg-red-800"
          >
            Reset All Data
          </button>
        ) : (
          <div className="rounded-md border border-red-300 bg-white p-4">
            <p className="text-sm font-medium text-red-800 mb-3">
              Are you sure? This will permanently delete every record in
              PostgreSQL, Neo4j, and Redis.
            </p>
            <div className="flex gap-3">
              <button
                onClick={handleResetAll}
                disabled={resetting}
                className="rounded-md bg-red-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {resetting ? "Resetting..." : "Yes, wipe everything"}
              </button>
              <button
                onClick={() => setResetConfirmOpen(false)}
                disabled={resetting}
                className="rounded-md bg-slate-200 px-5 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-300 disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Reset diagnostics panel
// ---------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ResetDetailsPanel({ details }: { details: Record<string, any> }) {
  const pg = details.postgres ?? {};
  const celery = details.celery ?? {};
  const redis = details.redis ?? {};
  const neo4j = details.neo4j ?? {};

  return (
    <div className="mb-4 rounded-md border border-slate-300 bg-white p-4 text-xs">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">
        Reset Diagnostics
      </h3>

      {/* PostgreSQL */}
      <div className="mb-3">
        <p className="font-semibold text-slate-600 mb-1">PostgreSQL</p>
        {pg.error ? (
          <p className="text-red-600 font-mono bg-red-50 px-2 py-1 rounded">
            ERROR: {pg.error}
          </p>
        ) : (
          <>
            <p className="text-green-700">
              Truncated: {pg.count ?? 0} table(s)
            </p>
            {pg.tables_skipped_not_found?.length > 0 && (
              <p className="text-amber-600 mt-1">
                Skipped (not in DB):{" "}
                <span className="font-mono">
                  {pg.tables_skipped_not_found.join(", ")}
                </span>
              </p>
            )}
            {pg.tables_failed && Object.keys(pg.tables_failed).length > 0 && (
              <div className="mt-1">
                <p className="text-red-600 font-semibold">Failed tables:</p>
                {Object.entries(pg.tables_failed).map(([table, err]) => (
                  <p key={table} className="text-red-500 font-mono ml-2">
                    {table}: {String(err)}
                  </p>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Celery */}
      <div className="mb-3">
        <p className="font-semibold text-slate-600 mb-1">Celery</p>
        {celery.error ? (
          <p className="text-amber-600 font-mono bg-amber-50 px-2 py-1 rounded">
            {celery.error}
          </p>
        ) : (
          <p className="text-green-700">
            Tasks revoked: {celery.tasks_revoked ?? 0}, Queues purged:{" "}
            {celery.queues_purged ? "yes" : "no"}
          </p>
        )}
      </div>

      {/* Redis */}
      <div className="mb-3">
        <p className="font-semibold text-slate-600 mb-1">Redis</p>
        {redis.error ? (
          <p className="text-amber-600 font-mono bg-amber-50 px-2 py-1 rounded">
            {redis.error}
          </p>
        ) : (
          <p className="text-green-700">
            Databases flushed: {(redis.databases_flushed ?? []).join(", ") || "none"}
          </p>
        )}
      </div>

      {/* Neo4j */}
      <div>
        <p className="font-semibold text-slate-600 mb-1">Neo4j</p>
        {neo4j.error ? (
          <p className="text-amber-600 font-mono bg-amber-50 px-2 py-1 rounded">
            {neo4j.error}
          </p>
        ) : (
          <p className="text-green-700">
            Nodes deleted: {neo4j.nodes_deleted ?? 0}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: "blue" | "green";
}) {
  const valueColor =
    highlight === "blue"
      ? "text-blue-600"
      : highlight === "green"
        ? "text-green-600"
        : "text-slate-900";
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </p>
      <p className={`text-lg font-bold tabular-nums ${valueColor}`}>{value}</p>
    </div>
  );
}

function Divider() {
  return <div className="hidden h-8 w-px bg-slate-200 sm:block" />;
}

function LogStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    completed: "bg-green-100 text-green-700",
    running: "bg-blue-100 text-blue-700",
    failed: "bg-red-100 text-red-700",
  };
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
        styles[status] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {status}
    </span>
  );
}

function formatTimeAgo(isoString: string): string {
  try {
    const d = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString();
  } catch {
    return isoString;
  }
}
