"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { fetchApi } from "@/lib/api";
import { Hypothesis } from "@/types";
import Link from "next/link";
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
} from "recharts";

function ScoreBar({
  label,
  score,
  color,
}: {
  label: string;
  score: number;
  color: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium text-gray-700">{label}</span>
        <span className="text-sm font-bold text-gray-900">{score.toFixed(1)}</span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2.5">
        <div
          className={`h-2.5 rounded-full ${color}`}
          style={{ width: `${Math.min(100, score)}%` }}
        />
      </div>
    </div>
  );
}

export default function HypothesisDetailPage() {
  const params = useParams();
  const [hypothesis, setHypothesis] = useState<Hypothesis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const h = await fetchApi<Hypothesis>(`/api/hypotheses/${params.id}`);
        setHypothesis(h);
      } catch (e: any) {
        setError(e.message || "Failed to load hypothesis");
      } finally {
        setLoading(false);
      }
    }
    if (params.id) load();
  }, [params.id]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (error || !hypothesis) {
    return (
      <div className="text-center py-12">
        <p className="text-red-600 mb-4">{error || "Hypothesis not found"}</p>
        <Link href="/hypotheses" className="text-blue-600 hover:underline">
          Back to hypotheses
        </Link>
      </div>
    );
  }

  const radarData = [
    { dimension: "Target Binding", score: hypothesis.target_binding_score },
    { dimension: "Pathway Overlap", score: hypothesis.pathway_overlap_score },
    { dimension: "Clinical Evidence", score: hypothesis.clinical_evidence_score },
  ];

  const strategyColors: Record<string, string> = {
    direct_target: "text-blue-600 bg-blue-50 border-blue-200",
    pathway: "text-purple-600 bg-purple-50 border-purple-200",
    clinical: "text-teal-600 bg-teal-50 border-teal-200",
  };
  const strategyClass = strategyColors[hypothesis.strategy || ""] || "text-gray-600 bg-gray-50 border-gray-200";

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link href="/hypotheses" className="text-sm text-gray-500 hover:text-gray-700 mb-2 inline-block">
          &larr; Back to hypotheses
        </Link>
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              {hypothesis.drug?.name || "Unknown Drug"}
            </h1>
            <p className="text-lg text-gray-600 mt-1">
              {hypothesis.cancer_type?.name || "Unknown Cancer"}
            </p>
          </div>
          <div className="text-right">
            <div className="text-4xl font-bold text-gray-900">
              {hypothesis.composite_score.toFixed(1)}
            </div>
            <p className="text-xs text-gray-500 mt-1">Composite Score</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Radar chart */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Score Dimensions</h2>
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="75%">
              <PolarGrid stroke="#e5e7eb" />
              <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 12 }} />
              <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
              <Radar
                name="Score"
                dataKey="score"
                stroke="#2563eb"
                fill="#3b82f6"
                fillOpacity={0.3}
                strokeWidth={2}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* Score breakdown */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Score Breakdown</h2>
          <div className="space-y-5">
            <ScoreBar
              label="Target Binding (40%)"
              score={hypothesis.target_binding_score}
              color="bg-blue-500"
            />
            <ScoreBar
              label="Pathway Overlap (35%)"
              score={hypothesis.pathway_overlap_score}
              color="bg-purple-500"
            />
            <ScoreBar
              label="Clinical Evidence (25%)"
              score={hypothesis.clinical_evidence_score}
              color="bg-teal-500"
            />
          </div>

          <div className="mt-6 pt-6 border-t border-gray-200">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-sm font-medium text-gray-500">Strategy:</span>
              <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-sm font-medium border ${strategyClass}`}>
                {(hypothesis.strategy || "unknown").replace("_", " ")}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-500">Status:</span>
              <span className="text-sm text-gray-700">{hypothesis.status}</span>
            </div>
          </div>
        </div>

        {/* Evidence summary */}
        {hypothesis.evidence_summary && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 lg:col-span-2">
            <h2 className="text-lg font-semibold text-gray-900 mb-3">Evidence Summary</h2>
            <div className="bg-gray-50 rounded-lg p-4">
              <p className="text-sm text-gray-700 leading-relaxed">{hypothesis.evidence_summary}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
