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
  pharmacological_response: {
    label: "Drug Sensitivity",
    description:
      "PRISM/GDSC drug sensitivity screen evidence from cancer cell line response rates",
    color: "#6366f1",
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

      {/* Statistical metadata + batch tracking */}
      {(h.statistical?.p_value != null || h.batch_id || h.statistical?.confidence_interval) && (
        <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
            Statistical &amp; Provenance Metadata
          </h3>
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {h.statistical?.p_value != null && (
              <div>
                <span className="text-xs text-slate-400">P-value: </span>
                <span className="font-mono font-medium text-slate-700">
                  {h.statistical.p_value < 0.001
                    ? h.statistical.p_value.toExponential(2)
                    : h.statistical.p_value.toFixed(4)}
                </span>
                {h.statistical.p_value < 0.05 && (
                  <span className="ml-1 text-xs text-emerald-600 font-medium">*</span>
                )}
              </div>
            )}
            {h.statistical?.fdr_adjusted_p_value != null && (
              <div>
                <span className="text-xs text-slate-400">FDR q-value: </span>
                <span className="font-mono font-medium text-slate-700">
                  {h.statistical.fdr_adjusted_p_value < 0.001
                    ? h.statistical.fdr_adjusted_p_value.toExponential(2)
                    : h.statistical.fdr_adjusted_p_value.toFixed(4)}
                </span>
              </div>
            )}
            {h.statistical?.confidence_interval && (
              <div>
                <span className="text-xs text-slate-400">95% CI: </span>
                <span className="font-mono font-medium text-slate-700">
                  [{h.statistical.confidence_interval.lower.toFixed(1)}, {h.statistical.confidence_interval.upper.toFixed(1)}]
                </span>
              </div>
            )}
            {h.statistical?.scoring_method_version && (
              <div>
                <span className="text-xs text-slate-400">Scoring: </span>
                <span className="text-xs font-medium text-slate-600">
                  {h.statistical.scoring_method_version}
                </span>
              </div>
            )}
            {h.batch_id && (
              <div>
                <span className="text-xs text-slate-400">Batch: </span>
                <span className="font-mono text-xs text-slate-600">
                  {h.batch_id.slice(0, 8)}
                </span>
              </div>
            )}
            {h.generation_pipeline_run_id && (
              <div>
                <span className="text-xs text-slate-400">Pipeline run: </span>
                <span className="text-xs font-medium text-slate-600">
                  #{h.generation_pipeline_run_id}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

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
        <StructuredAnalysisContent
          analysisType={analysis.analysis_type}
          content={content as Record<string, unknown>}
        />
      )}
    </div>
  );
}

function StructuredAnalysisContent({
  analysisType,
  content,
}: {
  analysisType: string;
  content: Record<string, unknown>;
}) {
  if (analysisType === "narrative") {
    return <NarrativeRenderer content={content} />;
  }
  if (analysisType === "critique") {
    return <CritiqueRenderer content={content} />;
  }
  if (analysisType === "confidence") {
    return <ConfidenceRenderer content={content} />;
  }
  // Fallback: render unknown structured content as labeled sections
  return <GenericStructuredRenderer content={content} />;
}

function NarrativeRenderer({ content }: { content: Record<string, unknown> }) {
  const title = content.title as string | undefined;
  const summary = content.summary as string | undefined;
  const sections: [string, string][] = [
    ["Molecular Mechanism", content.molecular_mechanism as string],
    ["Pathway Analysis", content.pathway_analysis as string],
    ["Expression Context", content.expression_context as string],
    ["Clinical Relevance", content.clinical_relevance as string],
  ].filter((s): s is [string, string] => typeof s[1] === "string" && s[1].length > 0);

  const keyGenes = content.key_genes as string[] | undefined;
  const keyPathways = content.key_pathways as string[] | undefined;
  const confidenceNote = content.confidence_note as string | undefined;

  // If content doesn't match expected shape, fall back
  if (!title && !summary && sections.length === 0) {
    return <GenericStructuredRenderer content={content} />;
  }

  return (
    <div className="space-y-4">
      {title && (
        <h4 className="text-base font-semibold text-slate-800">{title}</h4>
      )}
      {summary && (
        <p className="text-sm text-slate-600 leading-relaxed">{summary}</p>
      )}
      {sections.map(([heading, text]) => (
        <div key={heading}>
          <h5 className="text-sm font-semibold text-slate-700 mb-1">
            {heading}
          </h5>
          <div className="prose prose-sm prose-slate max-w-none">
            {text.split("\n").map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
        </div>
      ))}
      {keyGenes && keyGenes.length > 0 && (
        <div>
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Key Genes
          </span>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {keyGenes.map((g) => (
              <span
                key={g}
                className="rounded-full bg-purple-50 border border-purple-200 px-2 py-0.5 text-xs font-medium text-purple-700"
              >
                {g}
              </span>
            ))}
          </div>
        </div>
      )}
      {keyPathways && keyPathways.length > 0 && (
        <div>
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Key Pathways
          </span>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {keyPathways.map((p) => (
              <span
                key={p}
                className="rounded-full bg-blue-50 border border-blue-200 px-2 py-0.5 text-xs font-medium text-blue-700"
              >
                {p}
              </span>
            ))}
          </div>
        </div>
      )}
      {confidenceNote && (
        <div className="rounded-md bg-amber-50 border border-amber-200 px-4 py-3">
          <span className="text-xs font-semibold text-amber-700">
            Confidence Note
          </span>
          <p className="text-sm text-amber-800 mt-0.5">{confidenceNote}</p>
        </div>
      )}
    </div>
  );
}

function CritiqueRenderer({ content }: { content: Record<string, unknown> }) {
  const assessment = content.overall_assessment as string | undefined;
  const summary = content.summary as string | undefined;
  const weaknesses = content.mechanistic_weaknesses as
    | { concern: string; severity: string; explanation: string }[]
    | undefined;
  const gaps = content.evidence_gaps as
    | { gap: string; importance: string; suggestion: string }[]
    | undefined;
  const killCriteria = content.kill_criteria as string[] | undefined;
  const actions = content.recommended_actions as string[] | undefined;

  if (!assessment && !summary && !weaknesses && !gaps) {
    return <GenericStructuredRenderer content={content} />;
  }

  const severityStyle: Record<string, string> = {
    high: "bg-red-50 border-red-200 text-red-800",
    medium: "bg-amber-50 border-amber-200 text-amber-800",
    low: "bg-blue-50 border-blue-200 text-blue-800",
  };

  return (
    <div className="space-y-4">
      {assessment && (
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Assessment:
          </span>
          <span
            className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${
              assessment === "positive"
                ? "bg-emerald-100 text-emerald-700"
                : assessment === "cautious"
                  ? "bg-amber-100 text-amber-700"
                  : assessment === "negative"
                    ? "bg-red-100 text-red-700"
                    : "bg-slate-100 text-slate-600"
            }`}
          >
            {assessment}
          </span>
        </div>
      )}
      {summary && (
        <p className="text-sm text-slate-600 leading-relaxed">{summary}</p>
      )}
      {weaknesses && weaknesses.length > 0 && (
        <div>
          <h5 className="text-sm font-semibold text-slate-700 mb-2">
            Mechanistic Weaknesses
          </h5>
          <div className="space-y-2">
            {weaknesses.map((w, i) => (
              <div
                key={i}
                className={`rounded-md border px-4 py-3 ${
                  severityStyle[w.severity] ?? "bg-slate-50 border-slate-200 text-slate-700"
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-bold uppercase">
                    {w.severity}
                  </span>
                  <span className="text-sm font-medium">{w.concern}</span>
                </div>
                <p className="text-xs leading-relaxed opacity-90">
                  {w.explanation}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
      {gaps && gaps.length > 0 && (
        <div>
          <h5 className="text-sm font-semibold text-slate-700 mb-2">
            Evidence Gaps
          </h5>
          <div className="space-y-2">
            {gaps.map((g, i) => (
              <div
                key={i}
                className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`text-xs font-bold uppercase ${
                      g.importance === "critical"
                        ? "text-red-600"
                        : g.importance === "important"
                          ? "text-amber-600"
                          : "text-slate-500"
                    }`}
                  >
                    {g.importance}
                  </span>
                  <span className="text-sm font-medium text-slate-700">
                    {g.gap}
                  </span>
                </div>
                <p className="text-xs text-slate-600">{g.suggestion}</p>
              </div>
            ))}
          </div>
        </div>
      )}
      {killCriteria && killCriteria.length > 0 && (
        <div>
          <h5 className="text-sm font-semibold text-red-700 mb-2">
            Kill Criteria
          </h5>
          <ul className="space-y-1">
            {killCriteria.map((k, i) => (
              <li
                key={i}
                className="flex items-start gap-2 text-sm text-red-700"
              >
                <span className="mt-1 flex-shrink-0 h-1.5 w-1.5 rounded-full bg-red-400" />
                {k}
              </li>
            ))}
          </ul>
        </div>
      )}
      {actions && actions.length > 0 && (
        <div>
          <h5 className="text-sm font-semibold text-slate-700 mb-2">
            Recommended Actions
          </h5>
          <ul className="space-y-1">
            {actions.map((a, i) => (
              <li
                key={i}
                className="flex items-start gap-2 text-sm text-slate-600"
              >
                <span className="mt-1 flex-shrink-0 h-1.5 w-1.5 rounded-full bg-blue-400" />
                {a}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ConfidenceRenderer({ content }: { content: Record<string, unknown> }) {
  const overall = content.overall_confidence as number | undefined;
  const level = content.confidence_level as string | undefined;
  const summary = content.summary as string | undefined;
  const probabilities = content.probability_of_success as
    | Record<string, number>
    | undefined;
  const strengthOfEvidence = content.strength_of_evidence as
    | Record<string, string>
    | undefined;
  const recommendation = content.recommendation as string | undefined;

  if (overall == null && !summary && !probabilities) {
    return <GenericStructuredRenderer content={content} />;
  }

  return (
    <div className="space-y-4">
      {overall != null && (
        <div className="flex items-center gap-4">
          <div
            className={`flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-full text-xl font-bold text-white ${
              overall >= 70
                ? "bg-emerald-500"
                : overall >= 40
                  ? "bg-blue-500"
                  : overall >= 20
                    ? "bg-amber-500"
                    : "bg-slate-400"
            }`}
          >
            {overall}
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-800">
              Overall Confidence
            </p>
            {level && (
              <p className="text-xs text-slate-500 capitalize">{level}</p>
            )}
          </div>
        </div>
      )}
      {summary && (
        <p className="text-sm text-slate-600 leading-relaxed">{summary}</p>
      )}
      {probabilities && Object.keys(probabilities).length > 0 && (
        <div>
          <h5 className="text-sm font-semibold text-slate-700 mb-2">
            Probability of Success
          </h5>
          <div className="space-y-2">
            {Object.entries(probabilities).map(([stage, prob]) => (
              <div key={stage}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-xs font-medium text-slate-600 capitalize">
                    {stage.replace(/_/g, " ")}
                  </span>
                  <span className="text-xs tabular-nums font-bold text-slate-700">
                    {(prob * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="h-2 w-full rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      prob >= 0.6
                        ? "bg-emerald-500"
                        : prob >= 0.3
                          ? "bg-blue-500"
                          : "bg-amber-500"
                    }`}
                    style={{ width: `${Math.min(100, prob * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {strengthOfEvidence && Object.keys(strengthOfEvidence).length > 0 && (
        <div>
          <h5 className="text-sm font-semibold text-slate-700 mb-2">
            Strength of Evidence
          </h5>
          <div className="space-y-1.5">
            {Object.entries(strengthOfEvidence).map(([key, value]) => (
              <div key={key} className="text-sm">
                <span className="font-medium text-slate-600 capitalize">
                  {key.replace(/_/g, " ")}:
                </span>{" "}
                <span className="text-slate-500">{value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {recommendation && (
        <div className="rounded-md bg-blue-50 border border-blue-200 px-4 py-3">
          <span className="text-xs font-semibold text-blue-700">
            Recommendation
          </span>
          <p className="text-sm text-blue-800 mt-0.5 capitalize">
            {recommendation.replace(/_/g, " ")}
          </p>
        </div>
      )}
    </div>
  );
}

function GenericStructuredRenderer({
  content,
}: {
  content: Record<string, unknown>;
}) {
  return (
    <div className="space-y-3">
      {Object.entries(content).map(([key, value]) => {
        const label = key.replace(/_/g, " ");
        if (typeof value === "string") {
          return (
            <div key={key}>
              <h5 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-0.5">
                {label}
              </h5>
              <div className="prose prose-sm prose-slate max-w-none">
                {value.split("\n").map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
            </div>
          );
        }
        if (typeof value === "number") {
          return (
            <div key={key} className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                {label}:
              </span>
              <span className="text-sm font-medium text-slate-700">
                {value}
              </span>
            </div>
          );
        }
        if (Array.isArray(value) && value.every((v) => typeof v === "string")) {
          return (
            <div key={key}>
              <h5 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                {label}
              </h5>
              <ul className="space-y-0.5">
                {(value as string[]).map((item, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-2 text-sm text-slate-600"
                  >
                    <span className="mt-1.5 flex-shrink-0 h-1.5 w-1.5 rounded-full bg-slate-300" />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          );
        }
        // Complex nested objects: show as formatted JSON block
        return (
          <div key={key}>
            <h5 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
              {label}
            </h5>
            <pre className="overflow-x-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700">
              {JSON.stringify(value, null, 2)}
            </pre>
          </div>
        );
      })}
    </div>
  );
}
