"use client";

import { useCallback, useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ScatterChart,
  Scatter,
  ZAxis,
} from "recharts";
import { fetchApi } from "@/lib/api";
import type { CancerType, TopGene } from "@/types";

// ---------------------------------------------------------------
// Analysis page — expression analysis + pipeline triggers
// ---------------------------------------------------------------

export default function AnalysisPage() {
  const [cancerTypes, setCancerTypes] = useState<CancerType[]>([]);
  const [selectedCancer, setSelectedCancer] = useState<number | null>(null);
  const [topGenes, setTopGenes] = useState<TopGene[]>([]);
  const [loadingGenes, setLoadingGenes] = useState(false);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);

  // Load cancer types
  useEffect(() => {
    fetchApi<{ cancer_types: CancerType[] }>("/api/cancer-types?per_page=100")
      .then((d) => {
        setCancerTypes(d.cancer_types);
        if (d.cancer_types.length > 0) setSelectedCancer(d.cancer_types[0].id);
      })
      .catch(() => {});
  }, []);

  // Load top genes when cancer type changes
  const loadTopGenes = useCallback(async () => {
    if (!selectedCancer) return;
    setLoadingGenes(true);
    try {
      const data = await fetchApi<{ genes: TopGene[] }>(
        `/api/analysis/expression/${selectedCancer}/top-genes?limit=30&direction=both`
      );
      setTopGenes(data.genes);
    } catch {
      setTopGenes([]);
    } finally {
      setLoadingGenes(false);
    }
  }, [selectedCancer]);

  useEffect(() => {
    loadTopGenes();
  }, [loadTopGenes]);

  // Trigger analysis pipeline
  const triggerPipeline = async (endpoint: string, label: string) => {
    setTaskStatus(`Starting ${label}...`);
    try {
      const body: Record<string, unknown> = {};
      if (selectedCancer) body.cancer_type_id = selectedCancer;

      await fetchApi(`/api/ingestion/start`, {
        method: "POST",
        body: JSON.stringify({ source: endpoint }),
      });
      setTaskStatus(`${label} queued successfully.`);
      setTimeout(() => setTaskStatus(null), 5000);
    } catch {
      setTaskStatus(`Failed to start ${label}.`);
      setTimeout(() => setTaskStatus(null), 5000);
    }
  };

  // Separate up/down regulated genes
  const upGenes = topGenes.filter(
    (g) => g.alteration_type === "overexpression"
  );
  const downGenes = topGenes.filter(
    (g) => g.alteration_type === "underexpression"
  );

  // Volcano-style data (z-score vs frequency)
  const volcanoData = topGenes
    .filter((g) => g.expression_zscore != null && g.frequency_percent != null)
    .map((g) => ({
      gene: g.gene_symbol,
      zscore: g.expression_zscore!,
      frequency: g.frequency_percent!,
      direction: g.alteration_type,
    }));

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-2">Analysis</h1>
      <p className="text-slate-500 mb-6">
        Gene expression analysis, pathway activity, and pipeline controls.
      </p>

      {/* Cancer type selector + pipeline buttons */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <select
          value={selectedCancer ?? ""}
          onChange={(e) => setSelectedCancer(Number(e.target.value) || null)}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 max-w-xs"
        >
          <option value="">Select cancer type</option>
          {cancerTypes.map((ct) => (
            <option key={ct.id} value={ct.id}>
              {ct.name} ({ct.tcga_code})
            </option>
          ))}
        </select>

        <div className="flex gap-2">
          <PipelineButton
            label="Run Expression Analysis"
            onClick={() =>
              triggerPipeline("full_expression_analysis", "Expression Analysis")
            }
          />
          <PipelineButton
            label="Generate Hypotheses"
            onClick={() =>
              triggerPipeline("hypotheses_all", "Hypothesis Generation")
            }
          />
          <PipelineButton
            label="LLM Narratives"
            onClick={() =>
              triggerPipeline("llm_full_analysis", "LLM Full Analysis")
            }
          />
        </div>
      </div>

      {/* Task status banner */}
      {taskStatus && (
        <div className="mb-4 rounded-md bg-blue-50 border border-blue-200 px-4 py-2 text-sm text-blue-700">
          {taskStatus}
        </div>
      )}

      {selectedCancer && (
        <>
          {/* Volcano-style plot */}
          {volcanoData.length > 0 && (
            <div className="mb-6 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-700 mb-3">
                Expression Z-Score vs Frequency
              </h3>
              <ResponsiveContainer width="100%" height={300}>
                <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 20 }}>
                  <XAxis
                    type="number"
                    dataKey="zscore"
                    name="Z-Score"
                    tick={{ fontSize: 11 }}
                    label={{
                      value: "Expression Z-Score",
                      position: "bottom",
                      fontSize: 11,
                      fill: "#94a3b8",
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="frequency"
                    name="Frequency %"
                    tick={{ fontSize: 11 }}
                    label={{
                      value: "Frequency %",
                      angle: -90,
                      position: "insideLeft",
                      fontSize: 11,
                      fill: "#94a3b8",
                    }}
                  />
                  <ZAxis range={[40, 200]} />
                  <Tooltip
                    content={({ payload }) => {
                      if (!payload?.[0]?.payload) return null;
                      const d = payload[0].payload;
                      return (
                        <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
                          <p className="font-bold">{d.gene}</p>
                          <p>Z-score: {d.zscore.toFixed(2)}</p>
                          <p>Frequency: {d.frequency.toFixed(1)}%</p>
                          <p className="capitalize">{d.direction}</p>
                        </div>
                      );
                    }}
                  />
                  <Scatter data={volcanoData.filter((d) => d.direction === "overexpression")} fill="#ef4444" name="Overexpressed" />
                  <Scatter data={volcanoData.filter((d) => d.direction === "underexpression")} fill="#3b82f6" name="Underexpressed" />
                </ScatterChart>
              </ResponsiveContainer>
              <div className="flex justify-center gap-4 mt-2 text-xs text-slate-500">
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
                  Overexpressed
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-blue-500" />
                  Underexpressed
                </span>
              </div>
            </div>
          )}

          {/* Two column: up-regulated + down-regulated */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <GeneBarChart
              title="Top Overexpressed Genes"
              genes={upGenes.slice(0, 15)}
              color="#ef4444"
              loading={loadingGenes}
            />
            <GeneBarChart
              title="Top Underexpressed Genes"
              genes={downGenes.slice(0, 15)}
              color="#3b82f6"
              loading={loadingGenes}
            />
          </div>
        </>
      )}

      {!selectedCancer && (
        <div className="rounded-lg border border-slate-200 bg-white px-5 py-12 text-center text-sm text-slate-400 shadow-sm">
          Select a cancer type above to view expression analysis.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------

function PipelineButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-800 active:bg-slate-700"
    >
      {label}
    </button>
  );
}

function GeneBarChart({
  title,
  genes,
  color,
  loading,
}: {
  title: string;
  genes: TopGene[];
  color: string;
  loading: boolean;
}) {
  const data = genes.map((g) => ({
    name: g.gene_symbol,
    zscore: Math.abs(g.expression_zscore ?? 0),
    freq: g.frequency_percent ?? 0,
  }));

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">{title}</h3>
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <span className="inline-block h-5 w-5 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
        </div>
      ) : data.length === 0 ? (
        <div className="py-12 text-center text-sm text-slate-400">
          No expression data available.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(200, data.length * 24)}>
          <BarChart data={data} layout="vertical" margin={{ left: 50 }}>
            <XAxis type="number" tick={{ fontSize: 10 }} />
            <YAxis
              type="category"
              dataKey="name"
              width={60}
              tick={{ fontSize: 10, fill: "#64748b" }}
            />
            <Tooltip
              contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12 }}
              formatter={(value: number) => [value.toFixed(2), "|Z-score|"]}
            />
            <Bar dataKey="zscore" radius={[0, 3, 3, 0]}>
              {data.map((_, i) => (
                <Cell key={i} fill={color} fillOpacity={0.8} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
