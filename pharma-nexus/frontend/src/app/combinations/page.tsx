"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchApi } from "@/lib/api";

// ---------------------------------------------------------------
// Types
// ---------------------------------------------------------------

interface DrugRef {
  id: number;
  name: string | null;
  drugbank_id: string | null;
}

interface CancerRef {
  id: number;
  name: string | null;
  tcga_code: string | null;
}

interface CombinationHypothesis {
  id: number;
  drug_a: DrugRef;
  drug_b: DrugRef;
  cancer_type: CancerRef;
  synergy_score: number;
  synergy_classification: string;
  dimension_scores: {
    pathway_complementarity: number;
    target_non_overlap: number;
    synthetic_lethality: number;
    safety_compatibility: number;
    clinical_precedent: number;
  };
  rationale?: string;
  created_at: string | null;
}

interface CombinationStats {
  total_combinations: number;
  by_classification: Record<string, number>;
  average_scores: Record<string, number>;
}

interface CancerType {
  id: number;
  name: string;
  tcga_code: string;
}

// ---------------------------------------------------------------
// Page
// ---------------------------------------------------------------

export default function CombinationsPage() {
  const [combinations, setCombinations] = useState<CombinationHypothesis[]>([]);
  const [stats, setStats] = useState<CombinationStats | null>(null);
  const [cancerTypes, setCancerTypes] = useState<CancerType[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCancer, setSelectedCancer] = useState<number | null>(null);
  const [minSynergy, setMinSynergy] = useState(0);
  const [classFilter, setClassFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedCancer) params.set("cancer_type_id", String(selectedCancer));
      if (minSynergy > 0) params.set("min_synergy", String(minSynergy));
      if (classFilter) params.set("synergy_classification", classFilter);
      params.set("per_page", "50");

      const [combosRes, statsRes] = await Promise.all([
        fetchApi<{ combinations: CombinationHypothesis[] }>(
          `/api/combinations/?${params.toString()}`
        ),
        fetchApi<CombinationStats>("/api/combinations/stats"),
      ]);

      setCombinations(combosRes.combinations);
      setStats(statsRes);
    } catch {
      setCombinations([]);
    } finally {
      setLoading(false);
    }
  }, [selectedCancer, minSynergy, classFilter]);

  useEffect(() => {
    fetchApi<{ cancer_types: CancerType[] }>("/api/cancer-types")
      .then((r) => setCancerTypes(r.cancer_types))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const classColors: Record<string, string> = {
    synergistic: "bg-emerald-100 text-emerald-700",
    additive: "bg-blue-100 text-blue-700",
    uncertain: "bg-amber-100 text-amber-700",
    antagonistic: "bg-red-100 text-red-700",
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">
          Drug Combinations
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Predicted synergistic drug pairs based on pathway complementarity,
          target non-overlap, synthetic lethality, and clinical precedent.
        </p>
      </div>

      {/* Stats cards */}
      {stats && stats.total_combinations > 0 && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard
            label="Total Combinations"
            value={stats.total_combinations}
          />
          <StatCard
            label="Synergistic"
            value={stats.by_classification?.synergistic ?? 0}
            color="text-emerald-600"
          />
          <StatCard
            label="Additive"
            value={stats.by_classification?.additive ?? 0}
            color="text-blue-600"
          />
          <StatCard
            label="Avg Synergy"
            value={Math.round(stats.average_scores?.synergy ?? 0)}
            suffix="/100"
          />
        </div>
      )}

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <select
          value={selectedCancer ?? ""}
          onChange={(e) =>
            setSelectedCancer(e.target.value ? Number(e.target.value) : null)
          }
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm"
        >
          <option value="">All Cancer Types</option>
          {cancerTypes.map((ct) => (
            <option key={ct.id} value={ct.id}>
              {ct.name} ({ct.tcga_code})
            </option>
          ))}
        </select>

        <select
          value={classFilter}
          onChange={(e) => setClassFilter(e.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm"
        >
          <option value="">All Classifications</option>
          <option value="synergistic">Synergistic</option>
          <option value="additive">Additive</option>
          <option value="uncertain">Uncertain</option>
          <option value="antagonistic">Antagonistic</option>
        </select>

        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500">Min Score</label>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minSynergy}
            onChange={(e) => setMinSynergy(Number(e.target.value))}
            className="w-24"
          />
          <span className="text-xs tabular-nums text-slate-600 font-medium">
            {minSynergy}
          </span>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <span className="inline-block h-6 w-6 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
        </div>
      )}

      {/* Empty state */}
      {!loading && combinations.length === 0 && (
        <div className="rounded-lg border border-slate-200 bg-white px-5 py-16 text-center shadow-sm">
          <p className="text-slate-500">No drug combinations found.</p>
          <p className="mt-2 text-sm text-slate-400">
            Generate combinations from the Data Sources page by running
            &quot;combinations_cancer&quot; or &quot;combinations_all&quot;.
          </p>
        </div>
      )}

      {/* Combination list */}
      {!loading && combinations.length > 0 && (
        <div className="space-y-3">
          {combinations.map((combo) => (
            <div
              key={combo.id}
              className="rounded-lg border border-slate-200 bg-white shadow-sm transition-shadow hover:shadow-md"
            >
              {/* Header row */}
              <button
                onClick={() =>
                  setExpanded(expanded === combo.id ? null : combo.id)
                }
                className="flex w-full items-center gap-4 p-4 text-left"
              >
                {/* Synergy score circle */}
                <div
                  className={`flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full text-sm font-bold text-white ${
                    combo.synergy_score >= 60
                      ? "bg-emerald-500"
                      : combo.synergy_score >= 40
                        ? "bg-blue-500"
                        : combo.synergy_score >= 20
                          ? "bg-amber-500"
                          : "bg-slate-400"
                  }`}
                >
                  {Math.round(combo.synergy_score)}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-slate-900">
                      {combo.drug_a.name}
                    </span>
                    <span className="text-slate-400">+</span>
                    <span className="font-semibold text-slate-900">
                      {combo.drug_b.name}
                    </span>
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        classColors[combo.synergy_classification] ??
                        "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {combo.synergy_classification}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-4 text-xs text-slate-400">
                    <span>
                      {combo.cancer_type.name}{" "}
                      {combo.cancer_type.tcga_code && (
                        <span className="text-slate-300">
                          ({combo.cancer_type.tcga_code})
                        </span>
                      )}
                    </span>
                  </div>
                </div>

                {/* Mini dimension bars */}
                <div className="hidden sm:flex items-center gap-1">
                  {Object.entries(combo.dimension_scores).map(
                    ([key, value]) => (
                      <div
                        key={key}
                        className="w-8"
                        title={`${key}: ${Math.round(value)}`}
                      >
                        <div className="h-1.5 w-full rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-blue-500"
                            style={{
                              width: `${Math.min(100, value)}%`,
                            }}
                          />
                        </div>
                      </div>
                    )
                  )}
                </div>

                <svg
                  className={`h-5 w-5 flex-shrink-0 text-slate-400 transition-transform ${
                    expanded === combo.id ? "rotate-180" : ""
                  }`}
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m19.5 8.25-7.5 7.5-7.5-7.5"
                  />
                </svg>
              </button>

              {/* Expanded detail */}
              {expanded === combo.id && (
                <div className="border-t border-slate-100 px-4 pb-4 pt-3">
                  {/* Dimension score bars */}
                  <h4 className="text-xs font-semibold text-slate-500 mb-2">
                    SYNERGY DIMENSIONS
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 mb-4">
                    {SYNERGY_DIMENSIONS.map(({ key, label, color }) => {
                      const val =
                        combo.dimension_scores[
                          key as keyof typeof combo.dimension_scores
                        ] ?? 0;
                      return (
                        <div key={key}>
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="text-xs text-slate-600">
                              {label}
                            </span>
                            <span className="text-xs tabular-nums font-bold text-slate-700">
                              {Math.round(val)}
                            </span>
                          </div>
                          <div className="h-1.5 w-full rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full transition-all"
                              style={{
                                width: `${Math.min(100, val)}%`,
                                backgroundColor: color,
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Rationale */}
                  {combo.rationale && (
                    <div className="mt-3">
                      <h4 className="text-xs font-semibold text-slate-500 mb-1">
                        RATIONALE
                      </h4>
                      <p className="text-sm text-slate-600">
                        {combo.rationale}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------
// Constants & subcomponents
// ---------------------------------------------------------------

const SYNERGY_DIMENSIONS = [
  {
    key: "pathway_complementarity",
    label: "Pathway Complementarity",
    color: "#8b5cf6",
  },
  {
    key: "target_non_overlap",
    label: "Target Non-Overlap",
    color: "#0ea5e9",
  },
  {
    key: "synthetic_lethality",
    label: "Synthetic Lethality",
    color: "#f43f5e",
  },
  {
    key: "safety_compatibility",
    label: "Safety Compatibility",
    color: "#10b981",
  },
  {
    key: "clinical_precedent",
    label: "Clinical Precedent",
    color: "#f59e0b",
  },
];

function StatCard({
  label,
  value,
  color,
  suffix,
}: {
  label: string;
  value: number;
  color?: string;
  suffix?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`text-xl font-bold ${color || "text-slate-900"}`}>
        {value.toLocaleString()}
        {suffix && (
          <span className="text-sm font-normal text-slate-400">{suffix}</span>
        )}
      </p>
    </div>
  );
}
