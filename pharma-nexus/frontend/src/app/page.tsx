"use client";

import { useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";
import { Hypothesis, HypothesisStats } from "@/types";
import Link from "next/link";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

const COLORS = ["#2563eb", "#7c3aed", "#0891b2", "#059669", "#d97706"];

function StatCard({
  label,
  value,
  subtitle,
}: {
  label: string;
  value: string | number;
  subtitle?: string;
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <p className="text-sm font-medium text-gray-500">{label}</p>
      <p className="text-3xl font-bold text-gray-900 mt-2">{value}</p>
      {subtitle && <p className="text-xs text-gray-400 mt-1">{subtitle}</p>}
    </div>
  );
}

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 70
      ? "bg-green-100 text-green-800"
      : score >= 40
      ? "bg-yellow-100 text-yellow-800"
      : "bg-gray-100 text-gray-600";
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
      {score.toFixed(1)}
    </span>
  );
}

export default function DashboardPage() {
  const [drugCount, setDrugCount] = useState<number>(0);
  const [cancerCount, setCancerCount] = useState<number>(0);
  const [stats, setStats] = useState<HypothesisStats | null>(null);
  const [topHypotheses, setTopHypotheses] = useState<Hypothesis[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [drugs, cancers, hStats, topH] = await Promise.all([
          fetchApi<{ count: number }>("/api/drugs/count"),
          fetchApi<{ count: number }>("/api/cancer-types/count"),
          fetchApi<HypothesisStats>("/api/hypotheses/stats"),
          fetchApi<Hypothesis[]>("/api/hypotheses/top?limit=10"),
        ]);
        setDrugCount(drugs.count);
        setCancerCount(cancers.count);
        setStats(hStats);
        setTopHypotheses(topH);
      } catch (e) {
        console.error("Dashboard load error:", e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const strategyData = stats
    ? Object.entries(stats.by_strategy).map(([name, count]) => ({
        name: name.replace("_", " "),
        count,
      }))
    : [];

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 mt-1">
          Drug repurposing discovery overview
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <StatCard label="Drugs" value={drugCount} subtitle="FDA-approved oncology drugs" />
        <StatCard label="Cancer Types" value={cancerCount} subtitle="TCGA-classified cancers" />
        <StatCard
          label="Hypotheses"
          value={stats?.total || 0}
          subtitle="Generated repurposing candidates"
        />
        <StatCard
          label="Avg Score"
          value={stats?.average_score?.toFixed(1) || "—"}
          subtitle="Mean composite score"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Top hypotheses */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900">Top Hypotheses</h2>
            <Link
              href="/hypotheses"
              className="text-sm text-blue-600 hover:text-blue-800 font-medium"
            >
              View all
            </Link>
          </div>
          {topHypotheses.length === 0 ? (
            <p className="text-gray-400 text-sm py-8 text-center">
              No hypotheses generated yet. Run ingestion first.
            </p>
          ) : (
            <div className="space-y-3">
              {topHypotheses.map((h) => (
                <Link
                  key={h.id}
                  href={`/hypotheses/${h.id}`}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-gray-900 truncate">
                      {h.drug?.name || "Unknown Drug"}
                    </p>
                    <p className="text-xs text-gray-500 truncate">
                      {h.cancer_type?.name || "Unknown Cancer"} — {h.strategy?.replace("_", " ")}
                    </p>
                  </div>
                  <ScoreBadge score={h.composite_score} />
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Strategy distribution chart */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Hypotheses by Strategy
          </h2>
          {strategyData.length === 0 ? (
            <p className="text-gray-400 text-sm py-8 text-center">
              No data available yet.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={strategyData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 12 }}
                  tickLine={false}
                />
                <YAxis tick={{ fontSize: 12 }} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    borderRadius: "8px",
                    border: "1px solid #e5e7eb",
                    boxShadow: "0 4px 6px -1px rgba(0,0,0,0.1)",
                  }}
                />
                <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                  {strategyData.map((_, index) => (
                    <Cell key={index} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}
