"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import IngestionCard, {
  type SourceStatus,
} from "@/components/ingestion/IngestionCard";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Source definitions — sourceKey must match the backend task key
// ---------------------------------------------------------------

const sources: { name: string; sourceKey: string; description: string }[] = [
  {
    name: "DrugBank",
    sourceKey: "drugbank",
    description: "FDA-approved drug data, mechanisms, targets",
  },
  {
    name: "PubChem",
    sourceKey: "pubchem",
    description: "Chemical properties, bioassay results",
  },
  {
    name: "ChEMBL",
    sourceKey: "chembl",
    description: "Bioactivity data, binding affinities",
  },
  {
    name: "cBioPortal",
    sourceKey: "cbioportal",
    description: "TCGA cancer genomics — mutations, expression, CNA",
  },
  {
    name: "TCGA / GDC",
    sourceKey: "tcga",
    description: "Supplementary molecular profiles of 11,000+ tumors",
  },
  {
    name: "COSMIC",
    sourceKey: "cosmic",
    description: "Somatic mutations in cancer",
  },
  {
    name: "KEGG",
    sourceKey: "kegg",
    description: "Biological pathway maps",
  },
  {
    name: "Reactome",
    sourceKey: "reactome",
    description: "Pathway database with molecular details",
  },
  {
    name: "UniProt",
    sourceKey: "uniprot",
    description: "Protein function and interaction data",
  },
  {
    name: "PubMed",
    sourceKey: "literature",
    description: "Published biomedical literature",
  },
  {
    name: "ClinicalTrials.gov",
    sourceKey: "clinical_trials",
    description: "Active and completed trial data",
  },
  {
    name: "STRING",
    sourceKey: "string",
    description: "Protein-protein interaction networks",
  },
  {
    name: "OpenTargets",
    sourceKey: "opentargets",
    description: "Target-disease association evidence",
  },
];

// ---------------------------------------------------------------
// Page component
// ---------------------------------------------------------------

interface LiveStatusResponse {
  sources: Record<string, SourceStatus>;
}

export default function DataSourcesPage() {
  const [statuses, setStatuses] = useState<Record<string, SourceStatus>>({});
  const [startingSource, setStartingSource] = useState<string | null>(null);

  // Track whether any source is currently running so we can poll faster
  const anyRunning = Object.values(statuses).some(
    (s) => s.status === "running",
  );

  // ---- Polling ------------------------------------------------
  const fetchStatus = useCallback(async () => {
    try {
      const data = await fetchApi<LiveStatusResponse>(
        "/api/ingestion/live-status",
      );
      setStatuses(data.sources);
    } catch {
      // Silently ignore — will retry next interval
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  // Poll: 1.5 s while running, 10 s when idle
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    const ms = anyRunning ? 1500 : 10000;
    intervalRef.current = setInterval(fetchStatus, ms);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [anyRunning, fetchStatus]);

  // ---- Start ingestion ----------------------------------------
  const handleStart = async (sourceKey: string) => {
    setStartingSource(sourceKey);
    try {
      await fetchApi("/api/ingestion/start", {
        method: "POST",
        body: JSON.stringify({ source: sourceKey }),
      });
      // Immediately refetch to pick up the new "running" status
      await fetchStatus();
    } catch (err) {
      console.error("Failed to start ingestion:", err);
    } finally {
      setStartingSource(null);
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

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-2">Data Sources</h1>
      <p className="text-slate-500 mb-6">
        Manage data ingestion from public biomedical databases.
      </p>

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

      {/* Card grid */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {sources.map((source) => (
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
