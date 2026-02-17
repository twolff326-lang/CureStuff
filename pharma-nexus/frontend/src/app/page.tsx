"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";
import type { Hypothesis, HypothesisStats } from "@/types";

interface DashboardCounts {
  drugs: number;
  hypotheses: number;
  cancerTypes: number;
  literature: number;
}

const STRENGTH_COLORS: Record<string, string> = {
  strong: "bg-emerald-100 text-emerald-800",
  moderate: "bg-blue-100 text-blue-800",
  suggestive: "bg-amber-100 text-amber-800",
  speculative: "bg-slate-100 text-slate-600",
};

export default function DashboardPage() {
  const [counts, setCounts] = useState<DashboardCounts | null>(null);
  const [stats, setStats] = useState<HypothesisStats | null>(null);
  const [topHypotheses, setTopHypotheses] = useState<Hypothesis[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [drugsRes, cancerRes, statsRes, topRes] = await Promise.allSettled([
          fetchApi<{ total: number }>("/api/drugs?per_page=1"),
          fetchApi<{ total: number }>("/api/cancer-types?per_page=1"),
          fetchApi<HypothesisStats>("/api/hypotheses/stats"),
          fetchApi<{ hypotheses: Hypothesis[] }>("/api/hypotheses/top?limit=10"),
        ]);

        setCounts({
          drugs: drugsRes.status === "fulfilled" ? drugsRes.value.total : 0,
          cancerTypes: cancerRes.status === "fulfilled" ? cancerRes.value.total : 0,
          hypotheses:
            statsRes.status === "fulfilled"
              ? statsRes.value.total_hypotheses
              : 0,
          literature: 0, // Populated below if stats loaded
        });

        if (statsRes.status === "fulfilled") {
          setStats(statsRes.value);
        }

        if (topRes.status === "fulfilled") {
          setTopHypotheses(topRes.value.hypotheses);
        }
      } catch {
        // Dashboard degrades gracefully — individual cards show 0
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Dashboard</h1>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <StatCard
          title="Drugs Indexed"
          value={loading ? "..." : String(counts?.drugs ?? 0)}
        />
        <StatCard
          title="Hypotheses Generated"
          value={loading ? "..." : String(counts?.hypotheses ?? 0)}
        />
        <StatCard
          title="Cancer Types"
          value={loading ? "..." : String(counts?.cancerTypes ?? 0)}
        />
        <StatCard
          title="Avg Composite Score"
          value={
            loading
              ? "..."
              : stats
              ? String(Math.round(stats.average_scores.composite))
              : "0"
          }
        />
      </div>

      {/* Evidence Strength Distribution */}
      {stats && stats.total_hypotheses > 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-8">
          <h2 className="text-sm font-medium text-slate-500 mb-3">
            Evidence Strength Distribution
          </h2>
          <div className="flex gap-4">
            {Object.entries(stats.by_evidence_strength).map(([strength, count]) => (
              <div key={strength} className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-slate-600 capitalize">
                    {strength}
                  </span>
                  <span className="text-xs text-slate-400">{count as number}</span>
                </div>
                <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      strength === "strong"
                        ? "bg-emerald-500"
                        : strength === "moderate"
                        ? "bg-blue-500"
                        : strength === "suggestive"
                        ? "bg-amber-500"
                        : "bg-slate-400"
                    }`}
                    style={{
                      width: `${Math.min(
                        ((count as number) / stats.total_hypotheses) * 100,
                        100
                      )}%`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top Hypotheses */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-800">
            Top Hypotheses
          </h2>
          {topHypotheses.length > 0 && (
            <Link
              href="/hypotheses"
              className="text-sm text-blue-600 hover:text-blue-800"
            >
              View all
            </Link>
          )}
        </div>

        {loading && (
          <div className="text-center py-8">
            <div className="inline-block h-6 w-6 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
          </div>
        )}

        {!loading && topHypotheses.length === 0 && (
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-8 text-center">
            <p className="text-slate-500 mb-3">
              No hypotheses generated yet.
            </p>
            <Link
              href="/analysis"
              className="inline-block px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700"
            >
              Run Analysis
            </Link>
          </div>
        )}

        {!loading && topHypotheses.length > 0 && (
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 uppercase">
                    Hypothesis
                  </th>
                  <th className="px-4 py-2.5 text-center text-xs font-medium text-slate-500 uppercase w-20">
                    Score
                  </th>
                  <th className="px-4 py-2.5 text-center text-xs font-medium text-slate-500 uppercase w-24">
                    Strength
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {topHypotheses.map((h) => (
                  <tr key={h.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2.5">
                      <Link
                        href={`/hypotheses/${h.id}`}
                        className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                      >
                        {h.title}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <span className="text-sm font-bold text-slate-800">
                        {h.composite_score}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <span
                        className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                          STRENGTH_COLORS[h.evidence_strength] ??
                          "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {h.evidence_strength}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Top Cancer Types */}
      {stats && stats.top_cancer_types && stats.top_cancer_types.length > 0 && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">
            Top Cancer Types by Hypothesis Count
          </h2>
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5">
            <div className="space-y-2">
              {stats.top_cancer_types.slice(0, 10).map((ct) => (
                <div key={ct.name} className="flex items-center gap-3">
                  <span className="text-sm text-slate-700 w-48 truncate">
                    {ct.name}
                  </span>
                  <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-blue-500"
                      style={{
                        width: `${Math.min(
                          (ct.count / stats.top_cancer_types[0].count) * 100,
                          100
                        )}%`,
                      }}
                    />
                  </div>
                  <span className="text-xs text-slate-400 w-10 text-right">
                    {ct.count}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
      <p className="text-sm font-medium text-slate-500">{title}</p>
      <p className="text-2xl font-bold text-slate-900 mt-1">{value}</p>
    </div>
  );
}
