"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";

interface Proposal {
  id: number;
  drug_id: number;
  drug_name: string;
  cancer_type_id: number;
  cancer_name: string;
  mechanism_rationale: string;
  transitive_chain: string[];
  key_papers: string[];
  inferred_pathways: string[];
  confidence: number;
  novelty_reasoning: string | null;
  status: string;
  hypothesis_id: number | null;
  model_used: string;
  batch_id: string;
  created_at: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  proposed: "bg-amber-100 text-amber-800",
  hypothesis_created: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-600",
};

export default function DiscoveryPage() {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [minConfidence, setMinConfidence] = useState(0);

  const fetchProposals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      if (minConfidence > 0)
        params.set("min_confidence", String(minConfidence / 100));
      params.set("limit", "100");

      const data = await fetchApi<{ proposals: Proposal[] }>(
        `/api/discovery/proposals?${params}`
      );
      setProposals(data.proposals);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load proposals"
      );
    } finally {
      setLoading(false);
    }
  }, [statusFilter, minConfidence]);

  useEffect(() => {
    fetchProposals();
  }, [fetchProposals]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            LLM Discovery Proposals
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Novel drug-cancer connections inferred by Claude from literature
            synthesis
          </p>
        </div>
        <span className="text-sm text-slate-400">
          {proposals.length} proposals
        </span>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-4 mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Status
            </label>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">All</option>
              <option value="proposed">Proposed</option>
              <option value="hypothesis_created">Hypothesis Created</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              Min Confidence (%)
            </label>
            <input
              type="number"
              min={0}
              max={100}
              value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={fetchProposals}
            className="mt-2 text-sm text-red-600 underline"
          >
            Retry
          </button>
        </div>
      )}

      {loading && (
        <div className="text-center py-12">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
          <p className="mt-2 text-sm text-slate-500">Loading proposals...</p>
        </div>
      )}

      {!loading && !error && proposals.length === 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-12 text-center">
          <p className="text-slate-500">
            No discovery proposals yet. Run LLM Discovery from the{" "}
            <Link href="/analysis" className="text-blue-600 underline">
              Analysis page
            </Link>{" "}
            to have Claude reason about novel drug-cancer connections.
          </p>
        </div>
      )}

      {/* Proposals */}
      {!loading && proposals.length > 0 && (
        <div className="space-y-4">
          {proposals.map((p) => (
            <div
              key={p.id}
              className="bg-white rounded-lg shadow-sm border border-slate-200 p-5"
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="text-sm font-bold text-slate-900">
                    {p.drug_name} → {p.cancer_name}
                  </h3>
                  <div className="flex items-center gap-2 mt-1">
                    <span
                      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                        STATUS_COLORS[p.status] ?? "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {p.status.replace(/_/g, " ")}
                    </span>
                    {p.hypothesis_id && (
                      <Link
                        href={`/hypotheses/${p.hypothesis_id}`}
                        className="text-xs text-blue-600 underline"
                      >
                        View hypothesis
                      </Link>
                    )}
                    <span className="text-xs text-slate-400">
                      batch {p.batch_id}
                    </span>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xl font-bold text-violet-700">
                    {Math.round(p.confidence * 100)}%
                  </div>
                  <div className="text-xs text-slate-400">confidence</div>
                </div>
              </div>

              {/* Rationale */}
              <p className="text-sm text-slate-700 mb-3">
                {p.mechanism_rationale}
              </p>

              {/* Reasoning chain */}
              {p.transitive_chain.length > 0 && (
                <div className="mb-3">
                  <div className="text-xs font-medium text-slate-500 mb-1">
                    Reasoning Chain
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {p.transitive_chain.map((step, i) => (
                      <span key={i} className="flex items-center gap-1">
                        <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                          {step}
                        </span>
                        {i < p.transitive_chain.length - 1 && (
                          <span className="text-slate-300 text-xs">→</span>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata */}
              <div className="flex flex-wrap gap-4 text-xs text-slate-400">
                {p.inferred_pathways.length > 0 && (
                  <span>
                    Pathways: {p.inferred_pathways.join(", ")}
                  </span>
                )}
                {p.key_papers.length > 0 && (
                  <span>Papers: {p.key_papers.length} cited</span>
                )}
                <span>{p.model_used}</span>
                {p.created_at && (
                  <span>
                    {new Date(p.created_at).toLocaleDateString()}
                  </span>
                )}
              </div>

              {/* Novelty */}
              {p.novelty_reasoning && (
                <div className="mt-2 p-2 bg-amber-50 rounded text-xs text-amber-700">
                  <span className="font-medium">Why novel: </span>
                  {p.novelty_reasoning}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
