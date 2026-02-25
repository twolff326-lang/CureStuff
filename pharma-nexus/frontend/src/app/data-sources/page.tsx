"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { fetchApi } from "@/lib/api";
import { IngestionLog } from "@/types";

interface SourceConfig {
  key: string;
  name: string;
  description: string;
  icon: string;
}

const SOURCES: SourceConfig[] = [
  {
    key: "pubchem",
    name: "PubChem",
    description: "FDA-approved oncology drugs with chemical properties (SMILES, formula, CID)",
    icon: "M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z",
  },
  {
    key: "chembl",
    name: "ChEMBL",
    description: "Drug-target binding affinities (IC50, Ki, Kd) and mechanism of action",
    icon: "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
  },
  {
    key: "cancer_seed",
    name: "Cancer Types",
    description: "30 TCGA cancer types with top mutated genes and frequencies",
    icon: "M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z",
  },
  {
    key: "pathways",
    name: "Pathways",
    description: "20 cancer-relevant signaling pathways with gene memberships from Reactome",
    icon: "M13 10V3L4 14h7v7l9-11h-7z",
  },
  {
    key: "hypotheses",
    name: "Generate Hypotheses",
    description: "Run the hypothesis engine across all drug-cancer combinations",
    icon: "M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z",
  },
];

function ProgressBar({ processed, total }: { processed: number; total: number }) {
  const pct = total > 0 ? Math.min(100, (processed / total) * 100) : 0;
  return (
    <div className="mt-3">
      <div className="flex justify-between text-xs text-gray-500 mb-1">
        <span>{processed} / {total} records</span>
        <span>{pct.toFixed(0)}%</span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2">
        <div
          className="bg-blue-500 h-2 rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    completed: "bg-green-100 text-green-800",
    running: "bg-blue-100 text-blue-800 animate-pulse",
    failed: "bg-red-100 text-red-800",
    pending: "bg-gray-100 text-gray-600",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${styles[status] || styles.pending}`}>
      {status}
    </span>
  );
}

function Toast({ message, type, onClose }: { message: string; type: "success" | "error"; onClose: () => void }) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const t = setTimeout(() => onCloseRef.current(), 5000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className={`fixed bottom-6 right-6 z-50 flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg text-sm font-medium ${
      type === "success" ? "bg-green-600 text-white" : "bg-red-600 text-white"
    }`}>
      {message}
      <button onClick={onClose} className="ml-2 opacity-70 hover:opacity-100">&times;</button>
    </div>
  );
}

export default function DataSourcesPage() {
  const [logs, setLogs] = useState<IngestionLog[]>([]);
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  const loadLogs = useCallback(async () => {
    try {
      const result = await fetchApi<IngestionLog[]>("/api/ingestion/status");
      setLogs(result);
    } catch (e) {
      console.error("Failed to load ingestion status:", e);
    }
  }, []);

  useEffect(() => {
    loadLogs();
    const interval = setInterval(loadLogs, 3000);
    return () => clearInterval(interval);
  }, [loadLogs]);

  const getLatestLog = (source: string): IngestionLog | undefined => {
    return logs.find((l) => l.source === source);
  };

  const startIngestion = async (source: string) => {
    setLoading((prev) => ({ ...prev, [source]: true }));
    try {
      await fetchApi("/api/ingestion/start", {
        method: "POST",
        body: JSON.stringify({ source }),
      });
      setToast({ message: `Started ${source} ingestion`, type: "success" });
      await loadLogs();
    } catch (e: any) {
      setToast({ message: e.message || `Failed to start ${source}`, type: "error" });
    } finally {
      setLoading((prev) => ({ ...prev, [source]: false }));
    }
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Data Sources</h1>
        <p className="text-gray-500 mt-1">
          Manage data ingestion from biomedical databases
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        {SOURCES.map((source) => {
          const log = getLatestLog(source.key);
          const isRunning = log?.status === "running";
          const isButtonLoading = loading[source.key];

          return (
            <div
              key={source.key}
              className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 flex flex-col"
            >
              <div className="flex items-start gap-3 mb-3">
                <div className="p-2 bg-gray-100 rounded-lg flex-shrink-0">
                  <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={source.icon} />
                  </svg>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-semibold text-gray-900">{source.name}</h3>
                    {log && <StatusBadge status={log.status} />}
                  </div>
                  <p className="text-sm text-gray-500 mt-1">{source.description}</p>
                </div>
              </div>

              {/* Progress bar when running or completed */}
              {log && (log.status === "running" || log.status === "completed") && log.total_expected > 0 && (
                <ProgressBar processed={log.records_processed} total={log.total_expected} />
              )}

              {/* Error message */}
              {log?.status === "failed" && log.error_message && (
                <div className="mt-3 p-2 bg-red-50 rounded text-xs text-red-700 truncate" title={log.error_message}>
                  {log.error_message}
                </div>
              )}

              {/* Timestamp */}
              {log?.completed_at && (
                <p className="text-xs text-gray-400 mt-2">
                  Last run: {new Date(log.completed_at).toLocaleString()}
                </p>
              )}

              {/* Action button */}
              <div className="mt-auto pt-4">
                <button
                  onClick={() => startIngestion(source.key)}
                  disabled={isRunning || isButtonLoading}
                  className={`w-full px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isRunning || isButtonLoading
                      ? "bg-gray-100 text-gray-400 cursor-not-allowed"
                      : "bg-blue-600 text-white hover:bg-blue-700"
                  }`}
                >
                  {isRunning
                    ? "Running..."
                    : isButtonLoading
                    ? "Starting..."
                    : log?.status === "completed"
                    ? "Re-run"
                    : "Start"}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {toast && (
        <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />
      )}
    </div>
  );
}
