"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { fetchApi } from "@/lib/api";
import type { Hypothesis, HypothesisStats, IngestionLog } from "@/types";

// ---------------------------------------------------------------
// Dashboard — landing page with live stats
// ---------------------------------------------------------------

interface RecordCounts {
  drugs: number;
  targets: number;
  cancer_types: number;
  pathways: number;
  literature: number;
  clinical_trials: number;
  total: number;
  [key: string]: number;
}

export default function DashboardPage() {
  const [counts, setCounts] = useState<RecordCounts | null>(null);
  const [stats, setStats] = useState<HypothesisStats | null>(null);
  const [topHypotheses, setTopHypotheses] = useState<Hypothesis[]>([]);
  const [recentLogs, setRecentLogs] = useState<IngestionLog[]>([]);

  const loadAll = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchApi<{ counts: RecordCounts }>("/api/ingestion/record-counts"),
      fetchApi<HypothesisStats>("/api/hypotheses/stats"),
      fetchApi<{ hypotheses: Hypothesis[] }>("/api/hypotheses/top?limit=5"),
      fetchApi<{ logs: IngestionLog[] }>("/api/ingestion/logs?per_page=5"),
    ]);

    if (results[0].status === "fulfilled") setCounts(results[0].value.counts);
    if (results[1].status === "fulfilled") setStats(results[1].value);
    if (results[2].status === "fulfilled")
      setTopHypotheses(results[2].value.hypotheses);
    if (results[3].status === "fulfilled")
      setRecentLogs(results[3].value.logs);
  }, []);

  useEffect(() => {
    loadAll();
    const id = setInterval(loadAll, 30_000);
    return () => clearInterval(id);
  }, [loadAll]);

  // Score distribution for the bar chart
  const strengthData = stats
    ? Object.entries(stats.by_evidence_strength).map(([k, v]) => ({
        name: k,
        count: v,
      }))
    : [];

  const STRENGTH_COLORS: Record<string, string> = {
    strong: "#059669",
    moderate: "#2563eb",
    suggestive: "#d97706",
    speculative: "#94a3b8",
  };

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-2">Dashboard</h1>
      <p className="text-slate-500 mb-6">
        Drug repurposing discovery engine overview.
      </p>

      {/* Stat cards row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 mb-8">
        <StatCard
          title="Drugs Indexed"
          value={counts?.drugs}
          color="blue"
          href="/ingested-data"
        />
        <StatCard
          title="Hypotheses"
          value={stats?.total_hypotheses}
          color="emerald"
          href="/hypotheses"
        />
        <StatCard
          title="Cancer Types"
          value={counts?.cancer_types}
          color="purple"
        />
        <StatCard
          title="Literature Articles"
          value={counts?.literature}
          color="amber"
        />
      </div>

      {/* Second row: smaller stat cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6 mb-8">
        <MiniStat label="Targets" value={counts?.targets} />
        <MiniStat label="Pathways" value={counts?.pathways} />
        <MiniStat label="Clinical Trials" value={counts?.clinical_trials} />
        <MiniStat label="Total Records" value={counts?.total} />
        <MiniStat
          label="Avg Score"
          value={
            stats?.average_scores?.composite != null
              ? Math.round(stats.average_scores.composite)
              : undefined
          }
        />
        <MiniStat
          label="Strong Hits"
          value={stats?.by_evidence_strength?.strong}
        />
      </div>

      {/* Two-column layout: top hypotheses + charts */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Top hypotheses */}
        <div className="lg:col-span-2 rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
            <h2 className="text-sm font-semibold text-slate-700">
              Top Hypotheses
            </h2>
            <Link
              href="/hypotheses"
              className="text-xs font-medium text-blue-600 hover:text-blue-700"
            >
              View all
            </Link>
          </div>
          {topHypotheses.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-slate-400">
              No hypotheses generated yet.{" "}
              <Link
                href="/data-sources"
                className="text-blue-600 hover:underline"
              >
                Ingest data
              </Link>{" "}
              to get started.
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {topHypotheses.map((h) => (
                <Link
                  key={h.id}
                  href={`/hypotheses/${h.id}`}
                  className="flex items-center gap-4 px-5 py-3 hover:bg-slate-50 transition-colors"
                >
                  {/* Score circle */}
                  <div
                    className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full text-sm font-bold text-white ${
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
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-800 truncate">
                      {h.title}
                    </p>
                    <p className="text-xs text-slate-400 truncate">
                      {h.summary}
                    </p>
                  </div>
                  <StrengthBadge strength={h.evidence_strength} />
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Evidence strength distribution */}
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-5 py-4">
            <h2 className="text-sm font-semibold text-slate-700">
              Evidence Strength
            </h2>
          </div>
          {strengthData.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-slate-400">
              No data yet.
            </div>
          ) : (
            <div className="px-2 py-4">
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={strengthData} layout="vertical">
                  <XAxis type="number" hide />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={80}
                    tick={{ fontSize: 12, fill: "#64748b" }}
                  />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 8,
                      border: "1px solid #e2e8f0",
                    }}
                  />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                    {strengthData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={STRENGTH_COLORS[entry.name] ?? "#94a3b8"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Top cancer types below chart */}
          {stats?.top_cancer_types && stats.top_cancer_types.length > 0 && (
            <div className="border-t border-slate-100 px-5 py-3">
              <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-2">
                Top Cancer Types
              </p>
              <div className="space-y-1.5">
                {stats.top_cancer_types.slice(0, 5).map((ct) => (
                  <div
                    key={ct.name}
                    className="flex items-center justify-between text-xs"
                  >
                    <span className="text-slate-600 truncate">{ct.name}</span>
                    <span className="tabular-nums text-slate-400 font-medium">
                      {ct.count}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Recent ingestion activity */}
      <div className="mt-6 rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="text-sm font-semibold text-slate-700">
            Recent Ingestion Activity
          </h2>
        </div>
        {recentLogs.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-slate-400">
            No ingestion runs yet.
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                  Source
                </th>
                <th className="px-4 py-2 text-center text-xs font-medium uppercase tracking-wide text-slate-500">
                  Status
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wide text-slate-500">
                  Records
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium uppercase tracking-wide text-slate-500">
                  Time
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {recentLogs.map((log) => (
                <tr key={log.id}>
                  <td className="px-4 py-2 text-slate-700">{log.source}</td>
                  <td className="px-4 py-2 text-center">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        log.status === "completed"
                          ? "bg-green-100 text-green-700"
                          : log.status === "running"
                            ? "bg-blue-100 text-blue-700"
                            : log.status === "failed"
                              ? "bg-red-100 text-red-700"
                              : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {log.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-slate-600">
                    {log.records_processed.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right text-xs text-slate-400">
                    {log.started_at ? timeAgo(log.started_at) : "\u2014"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------

function StatCard({
  title,
  value,
  color,
  href,
}: {
  title: string;
  value: number | undefined;
  color: string;
  href?: string;
}) {
  const colorMap: Record<string, string> = {
    blue: "from-blue-500 to-blue-600",
    emerald: "from-emerald-500 to-emerald-600",
    purple: "from-purple-500 to-purple-600",
    amber: "from-amber-500 to-amber-600",
  };

  const card = (
    <div
      className={`rounded-lg bg-gradient-to-br ${colorMap[color] ?? colorMap.blue} p-5 text-white shadow-sm ${
        href ? "hover:shadow-md transition-shadow cursor-pointer" : ""
      }`}
    >
      <p className="text-sm font-medium text-white/80">{title}</p>
      <p className="mt-1 text-3xl font-bold tabular-nums">
        {value != null ? value.toLocaleString() : "\u2014"}
      </p>
    </div>
  );

  return href ? <Link href={href}>{card}</Link> : card;
}

function MiniStat({
  label,
  value,
}: {
  label: string;
  value: number | undefined;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-xs font-medium text-slate-400">{label}</p>
      <p className="text-lg font-bold tabular-nums text-slate-800">
        {value != null ? value.toLocaleString() : "\u2014"}
      </p>
    </div>
  );
}

function StrengthBadge({ strength }: { strength: string }) {
  const styles: Record<string, string> = {
    strong: "bg-emerald-100 text-emerald-700",
    moderate: "bg-blue-100 text-blue-700",
    suggestive: "bg-amber-100 text-amber-700",
    speculative: "bg-slate-100 text-slate-600",
  };
  return (
    <span
      className={`inline-block flex-shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
        styles[strength] ?? styles.speculative
      }`}
    >
      {strength}
    </span>
  );
}

function timeAgo(isoString: string): string {
  try {
    const diffMs = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return new Date(isoString).toLocaleDateString();
  } catch {
    return isoString;
  }
}
