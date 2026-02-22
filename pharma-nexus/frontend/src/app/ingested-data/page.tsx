"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

interface RecordCounts {
  drugs: number;
  targets: number;
  cancer_types: number;
  pathways: number;
  literature: number;
  clinical_trials: number;
  mutations: number;
  protein_interactions: number;
  bioassays: number;
  total: number;
  [key: string]: number;
}

interface PaginatedResponse<T> {
  total: number;
  page: number;
  per_page?: number;
  page_size?: number;
}

// Each tab definition
interface TabDef {
  key: string;
  label: string;
  countKey: string;
  endpoint: string;
  dataKey: string;
  columns: { key: string; label: string; truncate?: number }[];
  searchParam: string;
}

const TABS: TabDef[] = [
  {
    key: "drugs",
    label: "Drugs",
    countKey: "drugs",
    endpoint: "/api/drugs",
    dataKey: "drugs",
    searchParam: "search",
    columns: [
      { key: "drugbank_id", label: "DrugBank ID" },
      { key: "name", label: "Name" },
      { key: "generic_name", label: "Generic Name" },
      { key: "status", label: "Status" },
      { key: "indication", label: "Indication", truncate: 80 },
      { key: "molecular_formula", label: "Formula" },
    ],
  },
  {
    key: "targets",
    label: "Targets",
    countKey: "targets",
    endpoint: "/api/targets",
    dataKey: "targets",
    searchParam: "search",
    columns: [
      { key: "uniprot_id", label: "UniProt ID" },
      { key: "gene_symbol", label: "Gene" },
      { key: "gene_name", label: "Name", truncate: 60 },
      { key: "organism", label: "Organism" },
      { key: "protein_class", label: "Protein Class" },
      { key: "ensembl_gene_id", label: "Ensembl ID" },
    ],
  },
  {
    key: "cancer_types",
    label: "Cancer Types",
    countKey: "cancer_types",
    endpoint: "/api/cancer-types",
    dataKey: "cancer_types",
    searchParam: "search",
    columns: [
      { key: "tcga_code", label: "TCGA Code" },
      { key: "name", label: "Name" },
      { key: "tissue", label: "Tissue" },
      { key: "organ", label: "Organ" },
      { key: "sample_count", label: "Samples" },
      { key: "mutation_count", label: "Mutations" },
    ],
  },
  {
    key: "pathways",
    label: "Pathways",
    countKey: "pathways",
    endpoint: "/api/pathways",
    dataKey: "pathways",
    searchParam: "search",
    columns: [
      { key: "external_id", label: "ID" },
      { key: "name", label: "Name", truncate: 60 },
      { key: "source", label: "Source" },
      { key: "category", label: "Category" },
      { key: "gene_count", label: "Genes" },
    ],
  },
  {
    key: "literature",
    label: "Literature",
    countKey: "literature",
    endpoint: "/api/literature/search",
    dataKey: "results",
    searchParam: "q",
    columns: [
      { key: "pmid", label: "PMID" },
      { key: "title", label: "Title", truncate: 80 },
      { key: "journal", label: "Journal" },
      { key: "pub_date", label: "Date" },
      { key: "analysis_status", label: "Analysis" },
    ],
  },
  {
    key: "clinical_trials",
    label: "Clinical Trials",
    countKey: "clinical_trials",
    endpoint: "/api/clinical-trials/search",
    dataKey: "trials",
    searchParam: "drug",
    columns: [
      { key: "nct_id", label: "NCT ID" },
      { key: "title", label: "Title", truncate: 80 },
      { key: "status", label: "Status" },
      { key: "phase", label: "Phase" },
      { key: "enrollment", label: "Enrollment" },
      { key: "start_date", label: "Start Date" },
    ],
  },
];

const PER_PAGE = 25;

// ---------------------------------------------------------------
// Page component
// ---------------------------------------------------------------

export default function IngestedDataPage() {
  const [activeTab, setActiveTab] = useState(TABS[0].key);
  const [counts, setCounts] = useState<RecordCounts | null>(null);
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [loading, setLoading] = useState(false);

  const tab = TABS.find((t) => t.key === activeTab)!;

  // ---- Fetch counts -------------------------------------------
  const fetchCounts = useCallback(async () => {
    try {
      const data = await fetchApi<{ counts: RecordCounts }>(
        "/api/ingestion/record-counts",
      );
      setCounts(data.counts);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchCounts();
  }, [fetchCounts]);

  // Refresh counts periodically (30s)
  useEffect(() => {
    const id = setInterval(fetchCounts, 30_000);
    return () => clearInterval(id);
  }, [fetchCounts]);

  // ---- Debounce search ----------------------------------------
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 350);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [search]);

  // ---- Fetch rows for active tab ------------------------------
  const literatureCountRef = useRef(counts?.literature ?? 0);
  literatureCountRef.current = counts?.literature ?? 0;

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        params.set("page", String(page));

        // Literature search endpoint uses page_size, others use per_page
        if (tab.endpoint.includes("literature")) {
          params.set("top_k", String(PER_PAGE));
        } else if (tab.endpoint.includes("clinical-trials")) {
          params.set("page_size", String(PER_PAGE));
        } else {
          params.set("per_page", String(PER_PAGE));
        }

        if (debouncedSearch) {
          params.set(tab.searchParam, debouncedSearch);
        } else if (tab.key === "literature") {
          // Literature /search requires a query — fetch nothing if empty
          setRows([]);
          setTotal(literatureCountRef.current);
          setLoading(false);
          return;
        }

        const data = await fetchApi<Record<string, unknown>>(
          `${tab.endpoint}?${params.toString()}`,
        );

        if (cancelled) return;

        const items = (data[tab.dataKey] as Record<string, unknown>[]) ?? [];
        setRows(items);
        setTotal(
          (data.total as number) ??
            (data.count as number) ??
            items.length,
        );
      } catch {
        if (!cancelled) {
          setRows([]);
          setTotal(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [activeTab, page, debouncedSearch, tab]);

  // ---- Tab change resets --------------------------------------
  const handleTabChange = (key: string) => {
    setActiveTab(key);
    setPage(1);
    setSearch("");
    setDebouncedSearch("");
  };

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-2">
        Ingested Data
      </h1>
      <p className="text-slate-500 mb-6">
        Browse all records pulled from biomedical data sources.
      </p>

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b border-slate-200 mb-4">
        {TABS.map((t) => {
          const count = counts?.[t.countKey] ?? null;
          const isActive = t.key === activeTab;
          return (
            <button
              key={t.key}
              onClick={() => handleTabChange(t.key)}
              className={`relative px-4 py-2.5 text-sm font-medium transition-colors ${
                isActive
                  ? "text-blue-600 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-blue-600"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              {t.label}
              {count !== null && (
                <span
                  className={`ml-1.5 rounded-full px-2 py-0.5 text-xs tabular-nums ${
                    isActive
                      ? "bg-blue-100 text-blue-700"
                      : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {count.toLocaleString()}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Search bar */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={`Search ${tab.label.toLowerCase()}...`}
            className="w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
          <svg
            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>
        <span className="text-sm text-slate-500 tabular-nums">
          {total.toLocaleString()} record{total !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50">
              {tab.columns.map((col) => (
                <th
                  key={col.key}
                  className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td
                  colSpan={tab.columns.length}
                  className="px-4 py-12 text-center text-slate-400"
                >
                  <div className="flex items-center justify-center gap-2">
                    <svg
                      className="h-5 w-5 animate-spin text-blue-500"
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
                    Loading...
                  </div>
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={tab.columns.length}
                  className="px-4 py-12 text-center text-slate-400"
                >
                  {debouncedSearch
                    ? "No matching records found."
                    : tab.key === "literature"
                      ? "Enter a search term to browse literature."
                      : "No data ingested yet."}
                </td>
              </tr>
            ) : (
              rows.map((row, idx) => (
                <tr
                  key={(row.id as number) ?? idx}
                  className="hover:bg-slate-50 transition-colors"
                >
                  {tab.columns.map((col) => {
                    let val = row[col.key];
                    let display: string;
                    if (val == null) {
                      display = "—";
                    } else if (Array.isArray(val)) {
                      display = val.length ? val.join(", ") : "—";
                    } else {
                      display = String(val);
                    }
                    if (col.truncate && display.length > col.truncate) {
                      display = display.slice(0, col.truncate) + "...";
                    }
                    return (
                      <td
                        key={col.key}
                        className="whitespace-nowrap px-4 py-3 text-slate-700"
                        title={
                          col.truncate && String(val ?? "").length > col.truncate
                            ? String(val)
                            : undefined
                        }
                      >
                        {col.key === "status" || col.key === "analysis_status" ? (
                          <StatusPill value={display} />
                        ) : (
                          display
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-between">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-sm tabular-nums text-slate-500">
            Page {page} of {totalPages.toLocaleString()}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------

function StatusPill({ value }: { value: string }) {
  const lower = value.toLowerCase();
  let color = "bg-slate-100 text-slate-600";
  if (lower === "approved" || lower === "completed" || lower === "analyzed") {
    color = "bg-green-100 text-green-700";
  } else if (lower === "experimental" || lower === "running" || lower === "pending") {
    color = "bg-amber-100 text-amber-700";
  } else if (lower === "withdrawn" || lower === "failed" || lower === "terminated") {
    color = "bg-red-100 text-red-700";
  } else if (lower.includes("phase") || lower.includes("active") || lower === "recruiting") {
    color = "bg-blue-100 text-blue-700";
  }
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {value}
    </span>
  );
}
