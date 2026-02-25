"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi } from "@/lib/api";
import { Hypothesis, PaginatedResponse } from "@/types";
import Link from "next/link";

const STRATEGIES = ["all", "direct_target", "pathway", "clinical"];
const PER_PAGE = 20;

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 70
      ? "bg-green-100 text-green-800"
      : score >= 40
      ? "bg-yellow-100 text-yellow-800"
      : "bg-gray-100 text-gray-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${color}`}>
      {score.toFixed(1)}
    </span>
  );
}

function StrategyTag({ strategy }: { strategy: string | null }) {
  const colors: Record<string, string> = {
    direct_target: "bg-blue-100 text-blue-700",
    pathway: "bg-purple-100 text-purple-700",
    clinical: "bg-teal-100 text-teal-700",
  };
  const c = colors[strategy || ""] || "bg-gray-100 text-gray-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${c}`}>
      {(strategy || "unknown").replace("_", " ")}
    </span>
  );
}

export default function HypothesesPage() {
  const [data, setData] = useState<PaginatedResponse<Hypothesis> | null>(null);
  const [page, setPage] = useState(1);
  const [strategy, setStrategy] = useState("all");
  const [minScore, setMinScore] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(PER_PAGE),
        min_score: String(minScore),
        sort_by: "composite_score",
      });
      if (strategy !== "all") params.set("strategy", strategy);

      const result = await fetchApi<PaginatedResponse<Hypothesis>>(
        `/api/hypotheses?${params}`
      );
      setData(result);
    } catch (e) {
      console.error("Failed to load hypotheses:", e);
    } finally {
      setLoading(false);
    }
  }, [page, strategy, minScore]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = data ? Math.ceil(data.total / PER_PAGE) : 0;

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Hypotheses</h1>
        <p className="text-gray-500 mt-1">
          Drug repurposing candidates ranked by composite score
        </p>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 mb-6">
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => { setStrategy(e.target.value); setPage(1); }}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              {STRATEGIES.map((s) => (
                <option key={s} value={s}>
                  {s === "all" ? "All Strategies" : s.replace("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">
              Min Score: {minScore}
            </label>
            <input
              type="range"
              min={0}
              max={80}
              step={5}
              value={minScore}
              onChange={(e) => { setMinScore(Number(e.target.value)); setPage(1); }}
              className="w-40"
            />
          </div>
          {data && (
            <div className="ml-auto text-sm text-gray-500">
              {data.total} results
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-48">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="p-12 text-center text-gray-400">
            No hypotheses found. Run ingestion and hypothesis generation first.
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Drug</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Cancer Type</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Strategy</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Target</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Pathway</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Clinical</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Composite</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {data.items.map((h) => (
                <tr key={h.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <Link href={`/hypotheses/${h.id}`} className="text-sm font-medium text-blue-600 hover:text-blue-800">
                      {h.drug?.name || "—"}
                    </Link>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                    {h.cancer_type?.name || "—"}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <StrategyTag strategy={h.strategy} />
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                    {h.target_binding_score.toFixed(1)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                    {h.pathway_overlap_score.toFixed(1)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                    {h.clinical_evidence_score.toFixed(1)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <ScoreBadge score={h.composite_score} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-6 py-3 border-t border-gray-200 bg-gray-50">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1 rounded text-sm font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-sm text-gray-500">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1 rounded text-sm font-medium text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
