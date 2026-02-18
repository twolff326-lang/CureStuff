"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi } from "@/lib/api";
import { RECORDS_PROCESSED } from "@/lib/glossary";
import InfoTip from "@/components/common/InfoTip";
import type { IngestionLog } from "@/types";

// ------------------------------------------------------------------
// Source definitions (static metadata + ingestion source key)
// ------------------------------------------------------------------

interface SourceDef {
  name: string;
  key: string;                     // maps to ingestion API "source" param
  description: string;
  category: "drugs" | "cancer" | "pathways" | "literature" | "analysis";
  fallback?: string;               // describes what happens without the licensed file
}

const SOURCES: SourceDef[] = [
  // Drugs
  { name: "DrugBank", key: "drugbank", description: "FDA-approved drugs, mechanisms, targets, interactions", category: "drugs", fallback: "Without XML: loads 36 well-known drugs via PubChem API" },
  { name: "PubChem", key: "pubchem", description: "Chemical properties, bioassay results, compound data", category: "drugs" },
  { name: "ChEMBL", key: "chembl", description: "Bioactivity data, target-binding affinities", category: "drugs" },
  // Cancer
  { name: "TCGA", key: "tcga", description: "Molecular profiles of 11,000+ tumors across 33 cancer types", category: "cancer" },
  { name: "cBioPortal", key: "cbioportal", description: "Cancer genomics: mutations, copy number, expression", category: "cancer" },
  { name: "COSMIC", key: "cosmic", description: "Catalogue of somatic mutations in cancer", category: "cancer", fallback: "Without TSV: annotates 42 known cancer driver genes" },
  // Pathways
  { name: "KEGG", key: "kegg", description: "Biological pathway maps and molecular interactions", category: "pathways" },
  { name: "Reactome", key: "reactome", description: "Curated pathway database with molecular details", category: "pathways" },
  { name: "STRING", key: "string", description: "Protein-protein interaction networks and scores", category: "pathways" },
  { name: "UniProt", key: "uniprot", description: "Protein sequences, functions, classifications", category: "pathways" },
  { name: "OpenTargets", key: "opentargets", description: "Target-disease association evidence scores", category: "pathways" },
  // Literature
  { name: "PubMed", key: "literature", description: "Biomedical literature abstracts and metadata", category: "literature" },
  { name: "ClinicalTrials.gov", key: "clinical_trials", description: "Active and completed clinical trial data", category: "literature" },
  // Analysis
  { name: "Expression Analysis", key: "full_expression_analysis", description: "Differential expression, pathway activity, drug scores", category: "analysis" },
  { name: "Knowledge Graph", key: "knowledge_graph", description: "Sync all data to Neo4j for graph queries", category: "analysis" },
];

const CATEGORY_LABELS: Record<string, string> = {
  drugs: "Drug Databases",
  cancer: "Cancer Genomics",
  pathways: "Pathways & Interactions",
  literature: "Literature & Trials",
  analysis: "Computed Pipelines",
};

const CATEGORY_ORDER: string[] = ["drugs", "cancer", "pathways", "literature", "analysis"];

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  completed: { bg: "bg-emerald-100", text: "text-emerald-800", label: "Completed" },
  running: { bg: "bg-blue-100", text: "text-blue-800", label: "Running" },
  failed: { bg: "bg-red-100", text: "text-red-800", label: "Failed" },
  queued: { bg: "bg-amber-100", text: "text-amber-800", label: "Queued" },
  not_started: { bg: "bg-slate-100", text: "text-slate-600", label: "Not ingested" },
};

// ------------------------------------------------------------------
// Page component
// ------------------------------------------------------------------

export default function DataSourcesPage() {
  const [logs, setLogs] = useState<IngestionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [runningTasks, setRunningTasks] = useState<Record<string, string>>({}); // source -> task_id
  const [recentLogs, setRecentLogs] = useState<IngestionLog[]>([]);

  const loadLogs = useCallback(async () => {
    try {
      const res = await fetchApi<{ logs: IngestionLog[]; total: number }>(
        "/api/ingestion/logs?per_page=100"
      );
      setLogs(res.logs);
      setRecentLogs(res.logs.slice(0, 10));
    } catch { /* backend may not be running */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadLogs();
    // Poll for updates while tasks are running
    const interval = setInterval(() => {
      if (Object.keys(runningTasks).length > 0) {
        loadLogs();
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [loadLogs, runningTasks]);

  // Get latest log for a source
  const getSourceStatus = (sourceKey: string): IngestionLog | null => {
    return logs.find((l) => l.source === sourceKey) ?? null;
  };

  const startIngestion = async (source: SourceDef) => {
    try {
      const res = await fetchApi<{ task_id: string; status: string }>("/api/ingestion/start", {
        method: "POST",
        body: JSON.stringify({ source: source.key }),
      });
      setRunningTasks((prev) => ({ ...prev, [source.key]: res.task_id }));
      // Refresh logs after a short delay
      setTimeout(loadLogs, 2000);
    } catch { /* fail silently */ }
  };

  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    label: CATEGORY_LABELS[cat],
    sources: SOURCES.filter((s) => s.category === cat),
  }));

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Data Sources</h1>
          <p className="text-sm text-slate-500 mt-1">
            Manage ingestion from 13 biomedical databases. Each source runs as an async Celery task.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={loadLogs}
            className="px-3 py-1.5 text-sm bg-slate-100 text-slate-700 rounded-md hover:bg-slate-200"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Source cards by category */}
      {grouped.map((group) => (
        <div key={group.category} className="mb-8">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
            {group.label}
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {group.sources.map((source) => {
              const log = getSourceStatus(source.key);
              const isRunning = runningTasks[source.key] || log?.status === "running";
              const status = isRunning
                ? "running"
                : log?.status ?? "not_started";
              const style = STATUS_STYLES[status] ?? STATUS_STYLES.not_started;

              return (
                <div
                  key={source.key}
                  className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 flex flex-col"
                >
                  <div className="flex items-start justify-between mb-2">
                    <h3 className="font-semibold text-slate-800">{source.name}</h3>
                    <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${style.bg} ${style.text}`}>
                      {style.label}
                    </span>
                  </div>
                  <p className="text-sm text-slate-500 mb-2 flex-1">{source.description}</p>

                  {/* Fallback info for licensed sources */}
                  {source.fallback && (
                    <p className="text-xs text-amber-600 bg-amber-50 rounded px-2 py-1 mb-3">
                      {source.fallback}
                    </p>
                  )}

                  {/* Stats row */}
                  {log && log.status === "completed" && (
                    <div className="flex items-center gap-4 text-xs text-slate-400 mb-3">
                      <span>{log.records_processed.toLocaleString()} records<InfoTip text={RECORDS_PROCESSED} /></span>
                      {log.completed_at && (
                        <span>{formatTime(log.completed_at)}</span>
                      )}
                    </div>
                  )}

                  {/* Running indicator */}
                  {isRunning && (
                    <div className="flex items-center gap-2 text-xs text-blue-600 mb-3">
                      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600" />
                      Ingesting data...
                    </div>
                  )}

                  {/* Error row */}
                  {log?.status === "failed" && (
                    <p className="text-xs text-red-600 mb-3 truncate">
                      {log.errors || "Unknown error"}
                    </p>
                  )}

                  {/* Action button */}
                  <button
                    onClick={() => startIngestion(source)}
                    disabled={!!isRunning}
                    className={`w-full px-3 py-1.5 text-sm rounded-md transition-colors ${
                      isRunning
                        ? "bg-slate-100 text-slate-400 cursor-not-allowed"
                        : "bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200"
                    }`}
                  >
                    {isRunning
                      ? "Running..."
                      : log?.status === "completed"
                      ? "Re-ingest"
                      : "Start Ingestion"
                    }
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {/* Batch actions */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-8">
        <h2 className="text-sm font-medium text-slate-500 mb-3">Batch Ingestion</h2>
        <p className="text-xs text-slate-400 mb-3">
          Run entire categories at once. Each source runs as a separate async task.
        </p>
        <div className="flex flex-wrap gap-3">
          <BatchButton label="All Drugs" sourceKey="all_drugs" onStart={startIngestion} running={runningTasks} />
          <BatchButton label="All Cancer Data" sourceKey="all_cancer_data" onStart={startIngestion} running={runningTasks} />
          <BatchButton label="All Pathways" sourceKey="all_pathways" onStart={startIngestion} running={runningTasks} />
          <BatchButton label="All Literature" sourceKey="all_literature" onStart={startIngestion} running={runningTasks} />
        </div>
      </div>

      {/* Recent ingestion logs */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-200">
          <h2 className="text-sm font-medium text-slate-500">Recent Ingestion Activity</h2>
        </div>
        {loading && (
          <div className="p-8 text-center">
            <div className="inline-block h-5 w-5 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
          </div>
        )}
        {!loading && recentLogs.length === 0 && (
          <div className="p-8 text-center text-sm text-slate-400">
            No ingestion runs yet. Click a source above to start.
          </div>
        )}
        {!loading && recentLogs.length > 0 && (
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase">Source</th>
                <th className="px-4 py-2 text-center text-xs font-medium text-slate-500 uppercase">Status</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Records</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-slate-500 uppercase">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {recentLogs.map((log) => {
                const style = STATUS_STYLES[log.status] ?? STATUS_STYLES.not_started;
                return (
                  <tr key={log.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2 text-sm text-slate-700">{log.source}</td>
                    <td className="px-4 py-2 text-center">
                      <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${style.bg} ${style.text}`}>
                        {style.label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-sm text-slate-600">
                      {log.records_processed.toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-right text-xs text-slate-400">
                      {log.started_at ? formatTime(log.started_at) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Sub-components
// ------------------------------------------------------------------

function BatchButton({
  label,
  sourceKey,
  onStart,
  running,
}: {
  label: string;
  sourceKey: string;
  onStart: (source: SourceDef) => void;
  running: Record<string, string>;
}) {
  const isRunning = !!running[sourceKey];
  return (
    <button
      onClick={() => onStart({ name: label, key: sourceKey, description: "", category: "drugs" })}
      disabled={isRunning}
      className="px-4 py-2 text-sm bg-slate-800 text-white rounded-md hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {isRunning ? `${label}...` : label}
    </button>
  );
}

function formatTime(isoString: string): string {
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
