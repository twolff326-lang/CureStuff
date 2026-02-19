"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchApi } from "@/lib/api";
import type { Hypothesis, CancerType } from "@/types";

// ---------------------------------------------------------------
// Hypotheses list page
// ---------------------------------------------------------------

const PER_PAGE = 20;

const SORT_OPTIONS = [
  { label: "Score (high-low)", value: "score_desc" },
  { label: "Score (low-high)", value: "score_asc" },
  { label: "Newest", value: "newest" },
  { label: "Novelty", value: "novelty" },
];

export default function HypothesesPage() {
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState("score_desc");
  const [strengthFilter, setStrengthFilter] = useState<string>("all");
  const [cancerTypes, setCancerTypes] = useState<CancerType[]>([]);
  const [cancerFilter, setCancerFilter] = useState<string>("all");
  const [search, setSearch] = useState("");

  // Load cancer types for filter dropdown
  useEffect(() => {
    fetchApi<{ cancer_types: CancerType[] }>(
      "/api/cancer-types?per_page=100"
    ).then((d) => setCancerTypes(d.cancer_types)).catch(() => {});
  }, []);

  const loadHypotheses = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("page", String(page));
      params.set("per_page", String(PER_PAGE));

      if (sort === "score_desc") params.set("sort", "composite_score_desc");
      else if (sort === "score_asc") params.set("sort", "composite_score_asc");
      else if (sort === "newest") params.set("sort", "newest");
      else if (sort === "novelty") params.set("sort", "novelty_desc");

      if (strengthFilter !== "all")
        params.set("evidence_strength", strengthFilter);
      if (cancerFilter !== "all")
        params.set("cancer_type_id", cancerFilter);
      if (search) params.set("search", search);

      const data = await fetchApi<{
        hypotheses: Hypothesis[];
        total: number;
      }>(`/api/hypotheses?${params}`);
      setHypotheses(data.hypotheses);
      setTotal(data.total);
    } catch {
      setHypotheses([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, sort, strengthFilter, cancerFilter, search]);

  useEffect(() => {
    loadHypotheses();
  }, [loadHypotheses]);

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-3xl font-bold text-slate-900">
          Drug Repurposing Hypotheses
        </h1>
        <span className="text-sm tabular-nums text-slate-400">
          {total.toLocaleString()} total
        </span>
      </div>
      <p className="text-slate-500 mb-6">
        AI-generated hypotheses ranked by composite evidence score.
      </p>

      {/* Filters bar */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {/* Search */}
        <div className="relative flex-1 max-w-xs">
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search hypotheses..."
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

        {/* Strength filter */}
        <select
          value={strengthFilter}
          onChange={(e) => {
            setStrengthFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700"
        >
          <option value="all">All strengths</option>
          <option value="strong">Strong</option>
          <option value="moderate">Moderate</option>
          <option value="suggestive">Suggestive</option>
          <option value="speculative">Speculative</option>
        </select>

        {/* Cancer type filter */}
        <select
          value={cancerFilter}
          onChange={(e) => {
            setCancerFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 max-w-[200px]"
        >
          <option value="all">All cancer types</option>
          {cancerTypes.map((ct) => (
            <option key={ct.id} value={ct.id}>
              {ct.name} ({ct.tcga_code})
            </option>
          ))}
        </select>

        {/* Sort */}
        <select
          value={sort}
          onChange={(e) => {
            setSort(e.target.value);
            setPage(1);
          }}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50">
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 w-16">
                Score
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                Hypothesis
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 w-24">
                Strength
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 w-64">
                Dimension Scores
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td colSpan={4} className="px-4 py-12 text-center text-slate-400">
                  <span className="inline-block h-5 w-5 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600 mr-2 align-middle" />
                  Loading...
                </td>
              </tr>
            ) : hypotheses.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-12 text-center text-slate-400">
                  No hypotheses found.{" "}
                  <Link
                    href="/data-sources"
                    className="text-blue-600 hover:underline"
                  >
                    Ingest data
                  </Link>{" "}
                  and run the hypothesis engine.
                </td>
              </tr>
            ) : (
              hypotheses.map((h) => (
                <tr
                  key={h.id}
                  className="hover:bg-slate-50 transition-colors cursor-pointer"
                  onClick={() =>
                    (window.location.href = `/hypotheses/${h.id}`)
                  }
                >
                  {/* Score badge */}
                  <td className="px-4 py-3">
                    <div
                      className={`flex h-9 w-9 items-center justify-center rounded-full text-xs font-bold text-white ${
                        h.composite_score >= 60
                          ? "bg-emerald-500"
                          : h.composite_score >= 40
                            ? "bg-blue-500"
                            : h.composite_score >= 20
                              ? "bg-amber-500"
                              : "bg-slate-400"
                      }`}
                    >
                      {Math.round(h.composite_score)}
                    </div>
                  </td>

                  {/* Title + summary */}
                  <td className="px-4 py-3">
                    <p className="font-medium text-slate-800 truncate max-w-md">
                      {h.title}
                    </p>
                    {h.summary && (
                      <p className="text-xs text-slate-400 truncate max-w-md mt-0.5">
                        {h.summary}
                      </p>
                    )}
                  </td>

                  {/* Strength badge */}
                  <td className="px-4 py-3">
                    <StrengthBadge strength={h.evidence_strength} />
                  </td>

                  {/* Dimension score mini-bars */}
                  <td className="px-4 py-3">
                    <DimensionBars scores={h.dimension_scores} />
                  </td>
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
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <span className="text-sm tabular-nums text-slate-500">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------

function StrengthBadge({ strength }: { strength: string }) {
  const styles: Record<string, string> = {
    strong: "bg-emerald-100 text-emerald-700",
    moderate: "bg-blue-100 text-blue-700",
    suggestive: "bg-amber-100 text-amber-700",
    speculative: "bg-slate-100 text-slate-600",
  };
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
        styles[strength] ?? styles.speculative
      }`}
    >
      {strength}
    </span>
  );
}

const DIMENSION_LABELS: Record<string, { label: string; color: string }> = {
  pathway_overlap: { label: "Path", color: "bg-violet-500" },
  expression_correlation: { label: "Expr", color: "bg-sky-500" },
  literature_support: { label: "Lit", color: "bg-amber-500" },
  clinical_evidence: { label: "Clin", color: "bg-emerald-500" },
  safety: { label: "Safe", color: "bg-teal-500" },
  novelty: { label: "Nov", color: "bg-rose-500" },
};

function DimensionBars({
  scores,
}: {
  scores: Record<string, number> | undefined;
}) {
  if (!scores) return null;
  return (
    <div className="flex items-center gap-1.5">
      {Object.entries(DIMENSION_LABELS).map(([key, { label, color }]) => {
        const val = scores[key] ?? 0;
        return (
          <div key={key} className="flex-1 min-w-0" title={`${label}: ${Math.round(val)}`}>
            <div className="text-[10px] text-slate-400 mb-0.5 text-center">
              {label}
            </div>
            <div className="h-1.5 w-full rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${color}`}
                style={{ width: `${Math.min(100, val)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
