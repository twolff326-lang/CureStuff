"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { fetchApi } from "@/lib/api";
import type { Hypothesis, LLMAnalysis } from "@/types";

// ---------------------------------------------------------------
// Hypothesis detail page
// ---------------------------------------------------------------

const DIMENSION_META: Record<
  string,
  { label: string; description: string; color: string }
> = {
  pathway_overlap: {
    label: "Pathway Overlap",
    description: "Shared biological pathways between drug targets and cancer genes",
    color: "#8b5cf6",
  },
  expression_correlation: {
    label: "Expression",
    description: "Gene expression compatibility (inhibitor + overexpression = good)",
    color: "#0ea5e9",
  },
  literature_support: {
    label: "Literature",
    description: "Published evidence supporting this drug-cancer connection",
    color: "#f59e0b",
  },
  clinical_evidence: {
    label: "Clinical",
    description: "Clinical trial evidence and target-disease associations",
    color: "#10b981",
  },
  safety: {
    label: "Safety",
    description: "Drug approval status, mechanism of action, safety profile",
    color: "#14b8a6",
  },
  novelty: {
    label: "Novelty",
    description: "How novel this repurposing idea is (inverse of existing evidence)",
    color: "#f43f5e",
  },
  causal_dependency: {
    label: "Causal Dep.",
    description: "DepMap CRISPR evidence that drug targets are essential for cancer survival",
    color: "#dc2626",
  },
  mutation_context: {
    label: "Mutation Context",
    description:
      "Conditional vulnerability: drug target becomes essential due to cancer's specific mutations",
    color: "#c026d3",
  },
  polypharmacology: {
    label: "Polypharmacology",
    description:
      "Off-target bioassay activity against cancer-relevant proteins beyond official drug targets",
    color: "#0891b2",
  },
};

export default function HypothesisDetailPage() {
  const params = useParams();
  const id = params.id as string;

  const [hypothesis, setHypothesis] = useState<Hypothesis | null>(null);
  const [analyses, setAnalyses] = useState<LLMAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string>("overview");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [h, a] = await Promise.all([
        fetchApi<Hypothesis>(`/api/hypotheses/${id}`),
        fetchApi<{ analyses: LLMAnalysis[] }>(
          `/api/llm-analysis/hypothesis/${id}/analyses`
        ).catch(() => ({ analyses: [] })),
      ]);
      setHypothesis(h);
      setAnalyses(a.analyses);
    } catch {
      setHypothesis(null);
      setAnalyses([]);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="inline-block h-6 w-6 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
      </div>
    );
  }

  if (!hypothesis) {
    return (
      <div className="py-20 text-center">
        <p className="text-slate-500">Hypothesis not found.</p>
        <Link
          href="/hypotheses"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          Back to hypotheses
        </Link>
      </div>
    );
  }

  const h = hypothesis;
  const scores = h.dimension_scores;

  // Radar chart data
  const radarData = Object.entries(DIMENSION_META).map(([key, meta]) => ({
    dimension: meta.label,
    score: scores?.[key as keyof typeof scores] ?? 0,
    fullMark: 100,
  }));

  // Group analyses by type
  const narrativeAnalysis = analyses.find(
    (a) => a.analysis_type === "narrative"
  );
  const critiqueAnalysis = analyses.find(
    (a) => a.analysis_type === "critique"
  );
  const confidenceAnalysis = analyses.find(
    (a) => a.analysis_type === "confidence"
  );

  const TABS = [
    { key: "overview", label: "Overview" },
    { key: "evidence", label: `Evidence (${h.evidence_count ?? h.evidence?.length ?? 0})` },
    ...(narrativeAnalysis ? [{ key: "narrative", label: "LLM Narrative" }] : []),
    ...(critiqueAnalysis ? [{ key: "critique", label: "Critique" }] : []),
    ...(confidenceAnalysis ? [{ key: "confidence", label: "Confidence" }] : []),
  ];

  return (
    <div>
      {/* Breadcrumb */}
      <div className="mb-4">
        <Link
          href="/hypotheses"
          className="text-sm text-blue-600 hover:underline"
        >
          Hypotheses
        </Link>
        <span className="mx-2 text-slate-300">/</span>
        <span className="text-sm text-slate-500">#{h.id}</span>
      </div>

      {/* Header */}
      <div className="mb-6 flex items-start gap-4">
        {/* Score circle */}
        <div
          className={`flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-full text-lg font-bold text-white ${
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
          <h1 className="text-2xl font-bold text-slate-900">{h.title}</h1>
          {h.summary && (
            <p className="mt-1 text-sm text-slate-500">{h.summary}</p>
          )}
          <div className="mt-2 flex items-center gap-3">
            <StrengthBadge strength={h.evidence_strength} />
            {h.drug && (
              <span className="text-xs text-slate-400">
                Drug: <span className="font-medium text-slate-600">{h.drug.name}</span>
              </span>
            )}
            {h.cancer_type && (
              <span className="text-xs text-slate-400">
                Cancer: <span className="font-medium text-slate-600">{h.cancer_type.name}</span>
                {h.cancer_type.tcga_code && (
                  <span className="ml-1 text-slate-300">({h.cancer_type.tcga_code})</span>
                )}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Radar chart + dimension scores */}
      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Radar */}
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-700 mb-2">
            Evidence Profile
          </h3>
          <ResponsiveContainer width="100%" height={280}>
            <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="75%">
              <PolarGrid stroke="#e2e8f0" />
              <PolarAngleAxis
                dataKey="dimension"
                tick={{ fontSize: 11, fill: "#64748b" }}
              />
              <PolarRadiusAxis
                angle={30}
                domain={[0, 100]}
                tick={{ fontSize: 10, fill: "#94a3b8" }}
              />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #e2e8f0",
                  fontSize: 12,
                }}
              />
              <Radar
                name="Score"
                dataKey="score"
                stroke="#3b82f6"
                fill="#3b82f6"
                fillOpacity={0.2}
                strokeWidth={2}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* Score bars */}
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">
            Dimension Scores
          </h3>
          <div className="space-y-3">
            {Object.entries(DIMENSION_META).map(([key, meta]) => {
              const val = scores?.[key as keyof typeof scores] ?? 0;
              return (
                <div key={key}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-medium text-slate-600">
                      {meta.label}
                    </span>
                    <span className="text-xs tabular-nums font-bold text-slate-700">
                      {Math.round(val)}
                    </span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.min(100, val)}%`,
                        backgroundColor: meta.color,
                      }}
                    />
                  </div>
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    {meta.description}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-200 mb-4">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? "text-blue-600 border-b-2 border-blue-600"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "overview" && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">
            Mechanism Narrative
          </h3>
          {h.mechanism_narrative ? (
            <div className="prose prose-sm prose-slate max-w-none">
              {h.mechanism_narrative.split("\n").map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-400">
              No narrative generated yet. Run LLM analysis to generate one.
            </p>
          )}
        </div>
      )}

      {activeTab === "evidence" && (
        <div className="space-y-3">
          {!h.evidence || h.evidence.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white px-5 py-8 text-center text-sm text-slate-400 shadow-sm">
              No evidence records found.
            </div>
          ) : (
            h.evidence.map((ev) => (
              <div
                key={ev.id}
                className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div className="flex items-center gap-3 mb-2">
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                    {ev.evidence_type}
                  </span>
                  <span className="text-xs text-slate-400">
                    {ev.source_type}
                  </span>
                  <StrengthBadge strength={ev.strength} />
                  <span className="ml-auto text-xs tabular-nums text-slate-400">
                    conf: {(ev.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                {ev.description && (
                  <p className="text-sm text-slate-600">{ev.description}</p>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === "narrative" && narrativeAnalysis && (
        <AnalysisPanel analysis={narrativeAnalysis} />
      )}

      {activeTab === "critique" && critiqueAnalysis && (
        <AnalysisPanel analysis={critiqueAnalysis} />
      )}

      {activeTab === "confidence" && confidenceAnalysis && (
        <AnalysisPanel analysis={confidenceAnalysis} />
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

function AnalysisPanel({ analysis }: { analysis: LLMAnalysis }) {
  const content = analysis.content;
  const isString = typeof content === "string";

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-700 capitalize">
          {analysis.analysis_type.replace(/_/g, " ")}
        </h3>
        <div className="flex items-center gap-3 text-xs text-slate-400">
          <span>{analysis.model_used}</span>
          <span>{analysis.generation_time_seconds?.toFixed(1)}s</span>
          <span>
            {(
              (analysis.input_tokens + analysis.output_tokens) /
              1000
            ).toFixed(1)}k tokens
          </span>
        </div>
      </div>
      {isString ? (
        <div className="prose prose-sm prose-slate max-w-none">
          {(content as string).split("\n").map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      ) : (
        <pre className="overflow-x-auto rounded-md bg-slate-50 p-4 text-xs text-slate-700">
          {JSON.stringify(content, null, 2)}
        </pre>
      )}
    </div>
  );
}
