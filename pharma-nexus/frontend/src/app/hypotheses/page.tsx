"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";
import {
  ADJUSTED_SCORE,
  COMPOSITE_SCORE,
  NOVELTY_SCORE,
  LLM_CONFIDENCE,
  EVIDENCE_STRENGTH,
  MIN_SCORE_THRESHOLD,
} from "@/lib/glossary";
import InfoTip from "@/components/common/InfoTip";
import type { CancerType, Hypothesis } from "@/types";

type SortField = "adjusted_score" | "composite_score" | "novelty_score" | "created_at";

const STRENGTH_COLORS: Record<string, string> = {
  strong: "bg-emerald-100 text-emerald-800",
  moderate: "bg-blue-100 text-blue-800",
  suggestive: "bg-amber-100 text-amber-800",
  speculative: "bg-slate-100 text-slate-600",
};

export default function HypothesesPage() {
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [cancerTypes, setCancerTypes] = useState<CancerType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [sortBy, setSortBy] = useState<SortField>("adjusted_score");
  const [strengthFilter, setStrengthFilter] = useState<string>("");
  const [cancerFilter, setCancerFilter] = useState<string>("");
  const [minScore, setMinScore] = useState(0);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const perPage = 20;

  const fetchHypotheses = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
        sort_by: sortBy,
      });
      if (strengthFilter) params.set("evidence_strength", strengthFilter);
      if (cancerFilter) params.set("cancer_type_id", cancerFilter);
      if (minScore > 0) params.set("min_score", String(minScore));

      const data = await fetchApi<{
        hypotheses: Hypothesis[];
        total: number;
      }>(`/api/hypotheses?${params}`);
      setHypotheses(data.hypotheses);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load hypotheses");
    } finally {
      setLoading(false);
    }
  }, [page, sortBy, strengthFilter, cancerFilter, minScore]);

  useEffect(() => {
    fetchHypotheses();
  }, [fetchHypotheses]);

  useEffect(() => {
    fetchApi<{ cancer_types: CancerType[] }>(
      "/api/cancer-types?per_page=200"
    ).then((d) => setCancerTypes(d.cancer_types)).catch(() => {});
  }, []);

  const totalPages = Math.ceil(total / perPage);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            Drug Repurposing Hypotheses
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            {total} hypotheses found
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-4 mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Sort by<InfoTip text="Choose which score to rank hypotheses by. Adjusted Score incorporates AI review. Composite Score uses raw evidence only. Novelty Score ranks the most unstudied combinations first." />
            </label>
            <select
              value={sortBy}
              onChange={(e) => {
                setSortBy(e.target.value as SortField);
                setPage(1);
              }}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="adjusted_score">Adjusted Score</option>
              <option value="composite_score">Composite Score</option>
              <option value="novelty_score">Novelty Score</option>
              <option value="created_at">Date Created</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Evidence Strength<InfoTip text={EVIDENCE_STRENGTH} />
            </label>
            <select
              value={strengthFilter}
              onChange={(e) => {
                setStrengthFilter(e.target.value);
                setPage(1);
              }}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">All</option>
              <option value="strong">Strong</option>
              <option value="moderate">Moderate</option>
              <option value="suggestive">Suggestive</option>
              <option value="speculative">Speculative</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Cancer Type
            </label>
            <select
              value={cancerFilter}
              onChange={(e) => {
                setCancerFilter(e.target.value);
                setPage(1);
              }}
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
              Min Score<InfoTip text={MIN_SCORE_THRESHOLD} />
            </label>
            <input
              type="number"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => {
                setMinScore(Number(e.target.value));
                setPage(1);
              }}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={fetchHypotheses}
            className="mt-2 text-sm text-red-600 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="text-center py-12">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
          <p className="mt-2 text-sm text-slate-500">Loading hypotheses...</p>
        </div>
      )}

      {/* Empty State */}
      {!loading && !error && hypotheses.length === 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-12 text-center">
          <p className="text-slate-500">
            No hypotheses found. Run an analysis from the{" "}
            <Link href="/analysis" className="text-blue-600 underline">
              Analysis page
            </Link>{" "}
            to generate hypotheses.
          </p>
        </div>
      )}

      {/* Results Table */}
      {!loading && hypotheses.length > 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Hypothesis
                </th>
                <th className="px-4 py-3 text-center text-xs font-medium text-slate-500 uppercase tracking-wider w-24">
                  Score<InfoTip text={ADJUSTED_SCORE} />
                </th>
                <th className="px-4 py-3 text-center text-xs font-medium text-slate-500 uppercase tracking-wider w-20">
                  LLM<InfoTip text={LLM_CONFIDENCE} />
                </th>
                <th className="px-4 py-3 text-center text-xs font-medium text-slate-500 uppercase tracking-wider w-28">
                  Strength<InfoTip text={EVIDENCE_STRENGTH} />
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider w-28">
                  Created
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {hypotheses.map((h) => {
                const displayScore = h.adjusted_score ?? h.composite_score;
                return (
                <tr key={h.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/hypotheses/${h.id}`}
                      className="text-sm font-medium text-blue-600 hover:text-blue-800"
                    >
                      {h.title}
                    </Link>
                    {h.summary && (
                      <p className="text-xs text-slate-400 mt-0.5 line-clamp-1">
                        {h.summary}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <ScoreBadge score={displayScore} />
                    {h.adjusted_score != null && h.adjusted_score !== h.composite_score && (
                      <div className="text-xs text-slate-400 mt-0.5">
                        raw {h.composite_score}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {h.llm_confidence_score != null ? (
                      <span className="text-xs font-medium text-violet-700">
                        {h.llm_confidence_score}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-300">&mdash;</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span
                      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                        STRENGTH_COLORS[h.evidence_strength] ??
                        "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {h.evidence_strength}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-slate-400">
                    {new Date(h.created_at).toLocaleDateString()}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 bg-slate-50">
              <p className="text-xs text-slate-500">
                Page {page} of {totalPages}
              </p>
              <div className="flex gap-2">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="px-3 py-1 text-xs rounded border border-slate-300 bg-white hover:bg-slate-50 disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                  className="px-3 py-1 text-xs rounded border border-slate-300 bg-white hover:bg-slate-50 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScoreBadge({ score }: { score: number }) {
  let bg = "bg-slate-100 text-slate-700";
  if (score >= 70) bg = "bg-emerald-100 text-emerald-800";
  else if (score >= 50) bg = "bg-blue-100 text-blue-800";
  else if (score >= 30) bg = "bg-amber-100 text-amber-800";

  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${bg}`}>
      {score}
    </span>
  );
}
