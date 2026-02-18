"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { fetchApi } from "@/lib/api";
import {
  COMPOSITE_SCORE,
  ADJUSTED_SCORE,
  LLM_CONFIDENCE,
  EVIDENCE_STRENGTH,
  DIMENSION_TOOLTIPS,
  EVIDENCE_TYPE_TOOLTIPS,
  CONFIDENCE_PERCENT,
  RECOMMENDATION_TOOLTIPS,
} from "@/lib/glossary";
import InfoTip from "@/components/common/InfoTip";
import type { Hypothesis, HypothesisEvidence } from "@/types";

const DIMENSION_LABELS: Record<string, string> = {
  pathway_overlap: "Pathway Overlap",
  expression_correlation: "Expression Correlation",
  literature_support: "Literature Support",
  clinical_evidence: "Clinical Evidence",
  safety: "Safety Profile",
  novelty: "Novelty",
};

const STRENGTH_COLORS: Record<string, string> = {
  strong: "bg-emerald-100 text-emerald-800 border-emerald-200",
  moderate: "bg-blue-100 text-blue-800 border-blue-200",
  suggestive: "bg-amber-100 text-amber-800 border-amber-200",
  speculative: "bg-slate-100 text-slate-600 border-slate-200",
};

export default function HypothesisDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [hypothesis, setHypothesis] = useState<Hypothesis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const data = await fetchApi<Hypothesis>(`/api/hypotheses/${id}`);
        setHypothesis(data);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load hypothesis"
        );
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  if (loading) {
    return (
      <div className="text-center py-20">
        <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
        <p className="mt-2 text-sm text-slate-500">Loading hypothesis...</p>
      </div>
    );
  }

  if (error || !hypothesis) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-6">
        <p className="text-red-700">{error ?? "Hypothesis not found"}</p>
        <Link href="/hypotheses" className="text-sm text-red-600 underline mt-2 inline-block">
          Back to hypotheses
        </Link>
      </div>
    );
  }

  const h = hypothesis;
  const dims = h.dimension_scores;

  return (
    <div>
      {/* Breadcrumb */}
      <nav className="text-sm text-slate-400 mb-4">
        <Link href="/hypotheses" className="hover:text-slate-600">
          Hypotheses
        </Link>
        <span className="mx-2">/</span>
        <span className="text-slate-600">#{h.id}</span>
      </nav>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-slate-900">{h.title}</h1>
          <div className="flex items-center gap-3 mt-2">
            {h.drug && (
              <span className="text-sm text-slate-500">
                Drug: <span className="font-medium text-slate-700">{h.drug.name}</span>
              </span>
            )}
            {h.cancer_type && (
              <span className="text-sm text-slate-500">
                Cancer: <span className="font-medium text-slate-700">{h.cancer_type.name}</span>
                <span className="text-slate-400 ml-1">({h.cancer_type.tcga_code})</span>
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-4 ml-4">
          <div className="text-center">
            <div className="text-3xl font-bold text-slate-900">
              {h.adjusted_score ?? h.composite_score}
            </div>
            <div className="text-xs text-slate-400">
              {h.adjusted_score != null && h.adjusted_score !== h.composite_score
                ? <span>adjusted<InfoTip text={ADJUSTED_SCORE} /> (raw {h.composite_score})</span>
                : <span>/ 100<InfoTip text={COMPOSITE_SCORE} /></span>}
            </div>
          </div>
          {h.llm_confidence_score != null && (
            <div className="text-center border-l border-slate-200 pl-4">
              <div className="text-xl font-bold text-violet-700">
                {h.llm_confidence_score}
              </div>
              <div className="text-xs text-slate-400">LLM confidence<InfoTip text={LLM_CONFIDENCE} /></div>
            </div>
          )}
          <div className="flex flex-col items-center gap-1">
            <span
              className={`px-3 py-1 rounded-full text-sm font-medium border ${
                STRENGTH_COLORS[h.evidence_strength] ?? "bg-slate-100 text-slate-600 border-slate-200"
              }`}
            >
              {h.evidence_strength}<InfoTip text={EVIDENCE_STRENGTH} />
            </span>
            {h.llm_recommendation && (
              <span className="text-xs text-slate-500">
                {h.llm_recommendation.replace(/_/g, " ")}
                <InfoTip text={RECOMMENDATION_TOOLTIPS[h.llm_recommendation] ?? ""} />
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Summary */}
      {h.summary && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-6">
          <h2 className="text-sm font-medium text-slate-500 mb-2">Summary</h2>
          <p className="text-sm text-slate-700 leading-relaxed">{h.summary}</p>
        </div>
      )}

      {/* Dimension Scores */}
      {dims && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-6">
          <h2 className="text-sm font-medium text-slate-500 mb-4">
            Evidence Dimensions
            <InfoTip text="Six independent categories of evidence are scored from 0 to 100 and combined into the composite score. Each dimension captures a different type of scientific evidence." />
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Object.entries(DIMENSION_LABELS).map(([key, label]) => {
              const score = dims[key as keyof typeof dims] ?? 0;
              return (
                <div key={key} className="flex items-center gap-3">
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-slate-600">
                        {label}
                        {DIMENSION_TOOLTIPS[key] && <InfoTip text={DIMENSION_TOOLTIPS[key]} />}
                      </span>
                      <span className="text-xs font-bold text-slate-800">
                        {score}
                      </span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          score >= 70
                            ? "bg-emerald-500"
                            : score >= 40
                            ? "bg-blue-500"
                            : score >= 20
                            ? "bg-amber-500"
                            : "bg-slate-300"
                        }`}
                        style={{ width: `${score}%` }}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Mechanism Narrative */}
      {h.mechanism_narrative && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-6">
          <h2 className="text-sm font-medium text-slate-500 mb-2">
            Mechanistic Narrative
          </h2>
          <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
            {h.mechanism_narrative}
          </div>
        </div>
      )}

      {/* Evidence Records */}
      {h.evidence && h.evidence.length > 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5">
          <h2 className="text-sm font-medium text-slate-500 mb-4">
            Evidence ({h.evidence_count ?? h.evidence.length} records)
          </h2>
          <div className="space-y-3">
            {h.evidence.map((ev: HypothesisEvidence) => (
              <EvidenceCard key={ev.id} evidence={ev} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function EvidenceCard({ evidence }: { evidence: HypothesisEvidence }) {
  const strengthColor: Record<string, string> = {
    strong: "text-emerald-700 bg-emerald-50",
    moderate: "text-blue-700 bg-blue-50",
    weak: "text-slate-500 bg-slate-50",
  };

  const evTypeTip = EVIDENCE_TYPE_TOOLTIPS[evidence.evidence_type] ?? "";

  return (
    <div className="border border-slate-100 rounded-md p-3">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-medium text-slate-500 uppercase">
          {evidence.evidence_type.replace(/_/g, " ")}
          {evTypeTip && <InfoTip text={evTypeTip} />}
        </span>
        <span
          className={`text-xs px-1.5 py-0.5 rounded ${
            strengthColor[evidence.strength] ?? "text-slate-500 bg-slate-50"
          }`}
        >
          {evidence.strength}
        </span>
        <span className="text-xs text-slate-400 ml-auto">
          confidence: {(evidence.confidence * 100).toFixed(0)}%
          <InfoTip text={CONFIDENCE_PERCENT} />
        </span>
      </div>
      {evidence.description && (
        <p className="text-sm text-slate-600">{evidence.description}</p>
      )}
      {evidence.source_id && (
        <p className="text-xs text-slate-400 mt-1">
          Source: {evidence.source_type} / {evidence.source_id}
        </p>
      )}
    </div>
  );
}
