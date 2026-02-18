"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { fetchApi } from "@/lib/api";
import { KG_NODES, KG_EDGES, KG_NODE_TYPES, KG_EDGE_TYPES, KG_HOPS, EDGE_TYPE_TOOLTIPS } from "@/lib/glossary";
import InfoTip from "@/components/common/InfoTip";
import type { Drug, CancerType } from "@/types";

// ------------------------------------------------------------------
// Types for graph data
// ------------------------------------------------------------------

interface GraphNode {
  id: string;
  type: string;
  label: string;
  pg_id?: number;
  // Layout fields set by simulation
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

interface GraphEdge {
  source: string;
  target: string;
  type: string;
}

interface GraphStats {
  nodes: Record<string, number>;
  edges: Record<string, number>;
  total_nodes: number;
  total_edges: number;
}

// ------------------------------------------------------------------
// Constants
// ------------------------------------------------------------------

const NODE_COLORS: Record<string, string> = {
  Drug: "#3b82f6",
  Target: "#8b5cf6",
  Pathway: "#10b981",
  CancerType: "#ef4444",
  Gene: "#f59e0b",
  Paper: "#6b7280",
  ClinicalTrial: "#ec4899",
  Mutation: "#f97316",
};

const NODE_RADII: Record<string, number> = {
  Drug: 18,
  CancerType: 18,
  Target: 12,
  Gene: 12,
  Pathway: 14,
  Paper: 10,
  ClinicalTrial: 10,
  Mutation: 10,
};

const EDGE_COLORS: Record<string, string> = {
  TARGETS: "#8b5cf6",
  PARTICIPATES_IN: "#10b981",
  IS_TARGET: "#f59e0b",
  MUTATED_IN: "#ef4444",
  OVEREXPRESSED_IN: "#f97316",
  AMPLIFIED_IN: "#dc2626",
  DELETED_IN: "#9ca3af",
  INTERACTS_WITH: "#06b6d4",
  ASSOCIATED_WITH: "#64748b",
  MENTIONS_DRUG: "#3b82f6",
  STUDIES_CANCER: "#ef4444",
};

// ------------------------------------------------------------------
// Force simulation (simple spring-based layout)
// ------------------------------------------------------------------

function runForceLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  width: number,
  height: number,
  iterations: number = 120,
): GraphNode[] {
  const nodeMap = new Map<string, GraphNode>();
  nodes.forEach((n, i) => {
    // Initial positions in a circle
    const angle = (2 * Math.PI * i) / nodes.length;
    const r = Math.min(width, height) * 0.3;
    n.x = width / 2 + r * Math.cos(angle);
    n.y = height / 2 + r * Math.sin(angle);
    n.vx = 0;
    n.vy = 0;
    nodeMap.set(n.id, n);
  });

  for (let iter = 0; iter < iterations; iter++) {
    const alpha = 1 - iter / iterations;
    const repulsion = 2000 * alpha;
    const attraction = 0.02;
    const centerPull = 0.01;

    // Repulsion between all node pairs
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = (b.x ?? 0) - (a.x ?? 0);
        const dy = (b.y ?? 0) - (a.y ?? 0);
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const force = repulsion / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx! -= fx;
        a.vy! -= fy;
        b.vx! += fx;
        b.vy! += fy;
      }
    }

    // Attraction along edges
    for (const edge of edges) {
      const a = nodeMap.get(edge.source);
      const b = nodeMap.get(edge.target);
      if (!a || !b) continue;
      const dx = (b.x ?? 0) - (a.x ?? 0);
      const dy = (b.y ?? 0) - (a.y ?? 0);
      const fx = dx * attraction;
      const fy = dy * attraction;
      a.vx! += fx;
      a.vy! += fy;
      b.vx! -= fx;
      b.vy! -= fy;
    }

    // Center gravity
    for (const n of nodes) {
      n.vx! += (width / 2 - (n.x ?? 0)) * centerPull;
      n.vy! += (height / 2 - (n.y ?? 0)) * centerPull;
    }

    // Apply velocities with damping
    const damping = 0.6;
    for (const n of nodes) {
      n.vx! *= damping;
      n.vy! *= damping;
      n.x = Math.max(30, Math.min(width - 30, (n.x ?? 0) + n.vx!));
      n.y = Math.max(30, Math.min(height - 30, (n.y ?? 0) + n.vy!));
    }
  }

  return nodes;
}

// ------------------------------------------------------------------
// Page component
// ------------------------------------------------------------------

export default function KnowledgeGraphPage() {
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [statsError, setStatsError] = useState(false);
  const [drugs, setDrugs] = useState<Drug[]>([]);
  const [cancers, setCancers] = useState<CancerType[]>([]);
  const [selectedDrug, setSelectedDrug] = useState("");
  const [selectedCancer, setSelectedCancer] = useState("");
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [pathDrug, setPathDrug] = useState("");
  const [pathCancer, setPathCancer] = useState("");
  const [paths, setPaths] = useState<Array<{ node_sequence: Array<{ name: string; labels: string[] }>; hops: number }>>([]);
  const [pathLoading, setPathLoading] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  const WIDTH = 900;
  const HEIGHT = 560;

  // Load drugs and cancers for selectors
  useEffect(() => {
    async function loadOptions() {
      try {
        const [drugRes, cancerRes] = await Promise.allSettled([
          fetchApi<{ drugs: Drug[]; total: number }>("/api/drugs?per_page=50"),
          fetchApi<{ cancer_types: CancerType[]; total: number }>("/api/cancer-types?per_page=50"),
        ]);
        if (drugRes.status === "fulfilled") setDrugs(drugRes.value.drugs);
        if (cancerRes.status === "fulfilled") setCancers(cancerRes.value.cancer_types);
      } catch { /* degrade gracefully */ }
    }
    loadOptions();
  }, []);

  // Load stats
  useEffect(() => {
    async function loadStats() {
      try {
        const s = await fetchApi<GraphStats>("/knowledge-graph/stats");
        setStats(s);
      } catch {
        setStatsError(true);
      }
    }
    loadStats();
  }, []);

  // Fetch drug neighborhood
  const loadDrugGraph = useCallback(async (drugbankId: string) => {
    setLoading(true);
    try {
      const data = await fetchApi<{ nodes: GraphNode[]; edges: GraphEdge[] }>(
        `/knowledge-graph/drug/${drugbankId}/neighborhood?depth=2`
      );
      const laidOut = runForceLayout(data.nodes, data.edges, WIDTH, HEIGHT);
      setGraphNodes(laidOut);
      setGraphEdges(data.edges);
    } catch {
      setGraphNodes([]);
      setGraphEdges([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch cancer neighborhood
  const loadCancerGraph = useCallback(async (tcgaCode: string) => {
    setLoading(true);
    try {
      const data = await fetchApi<{ nodes: GraphNode[]; edges: GraphEdge[] }>(
        `/knowledge-graph/cancer/${tcgaCode}/neighborhood?depth=1`
      );
      const laidOut = runForceLayout(data.nodes, data.edges, WIDTH, HEIGHT);
      setGraphNodes(laidOut);
      setGraphEdges(data.edges);
    } catch {
      setGraphNodes([]);
      setGraphEdges([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMsg("");
    try {
      const res = await fetchApi<{ task_id: string }>("/knowledge-graph/sync", { method: "POST" });
      setSyncMsg(`Sync queued (task ${res.task_id.slice(0, 8)}...)`);
    } catch {
      setSyncMsg("Sync failed — is Celery running?");
    } finally {
      setSyncing(false);
    }
  };

  const findPaths = async () => {
    if (!pathDrug || !pathCancer) return;
    setPathLoading(true);
    setPaths([]);
    try {
      const res = await fetchApi<{ paths: Array<{ node_sequence: Array<{ name: string; labels: string[] }>; hops: number }> }>(
        `/knowledge-graph/paths?drug_id=${pathDrug}&cancer_id=${pathCancer}&max_hops=3`
      );
      setPaths(res.paths);
    } catch {
      setPaths([]);
    } finally {
      setPathLoading(false);
    }
  };

  const noGraph = graphNodes.length === 0 && !loading;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Knowledge Graph</h1>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-3 py-1.5 text-sm bg-slate-100 text-slate-700 rounded-md hover:bg-slate-200 disabled:opacity-50"
        >
          {syncing ? "Syncing..." : "Sync to Neo4j"}
        </button>
      </div>
      {syncMsg && <p className="text-sm text-slate-500 mb-4">{syncMsg}</p>}

      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {statsError ? (
          <div className="col-span-4 bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
            Neo4j not connected. Click "Sync to Neo4j" after data is ingested to populate the knowledge graph.
          </div>
        ) : stats ? (
          <>
            <MiniStat label="Total Nodes" value={stats.total_nodes.toLocaleString()} tooltip={KG_NODES} />
            <MiniStat label="Total Edges" value={stats.total_edges.toLocaleString()} tooltip={KG_EDGES} />
            <MiniStat label="Node Types" value={String(Object.keys(stats.nodes).length)} tooltip={KG_NODE_TYPES} />
            <MiniStat label="Edge Types" value={String(Object.keys(stats.edges).length)} tooltip={KG_EDGE_TYPES} />
          </>
        ) : (
          <div className="col-span-4 text-sm text-slate-400">Loading stats...</div>
        )}
      </div>

      {/* Node type breakdown */}
      {stats && stats.total_nodes > 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-4 mb-6">
          <h2 className="text-sm font-medium text-slate-500 mb-3">Graph Composition</h2>
          <div className="flex flex-wrap gap-3">
            {Object.entries(stats.nodes).map(([type, count]) => (
              <span key={type} className="flex items-center gap-1.5 text-sm">
                <span
                  className="w-3 h-3 rounded-full inline-block"
                  style={{ backgroundColor: NODE_COLORS[type] ?? "#94a3b8" }}
                />
                <span className="text-slate-700">{type}</span>
                <span className="text-slate-400">{count.toLocaleString()}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Explorer controls */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 mb-6">
        <h2 className="text-sm font-medium text-slate-500 mb-3">Graph Explorer</h2>
        <div className="flex flex-wrap gap-4">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs text-slate-500 mb-1">Drug Neighborhood</label>
            <select
              value={selectedDrug}
              onChange={(e) => {
                setSelectedDrug(e.target.value);
                setSelectedCancer("");
                if (e.target.value) loadDrugGraph(e.target.value);
              }}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm"
            >
              <option value="">Select a drug...</option>
              {drugs.map((d) => (
                <option key={d.drugbank_id} value={d.drugbank_id}>
                  {d.name} ({d.drugbank_id})
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs text-slate-500 mb-1">Cancer Neighborhood</label>
            <select
              value={selectedCancer}
              onChange={(e) => {
                setSelectedCancer(e.target.value);
                setSelectedDrug("");
                if (e.target.value) loadCancerGraph(e.target.value);
              }}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm"
            >
              <option value="">Select a cancer type...</option>
              {cancers.map((c) => (
                <option key={c.tcga_code} value={c.tcga_code}>
                  {c.name} ({c.tcga_code})
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* SVG graph visualization */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden mb-6">
        {loading && (
          <div className="flex items-center justify-center h-[560px]">
            <div className="inline-block h-6 w-6 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600" />
          </div>
        )}

        {noGraph && (
          <div className="flex items-center justify-center h-[560px] text-slate-400 text-sm">
            {statsError || (stats && stats.total_nodes === 0)
              ? "Graph is empty. Ingest data and sync to Neo4j first."
              : "Select a drug or cancer type above to explore the graph."}
          </div>
        )}

        {!loading && graphNodes.length > 0 && (
          <div className="relative">
            <svg ref={svgRef} width={WIDTH} height={HEIGHT} className="w-full" viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
              {/* Edges */}
              {graphEdges.map((e, i) => {
                const sNode = graphNodes.find((n) => n.id === e.source);
                const tNode = graphNodes.find((n) => n.id === e.target);
                if (!sNode || !tNode) return null;
                return (
                  <line
                    key={`e-${i}`}
                    x1={sNode.x}
                    y1={sNode.y}
                    x2={tNode.x}
                    y2={tNode.y}
                    stroke={EDGE_COLORS[e.type] ?? "#d1d5db"}
                    strokeWidth={1.5}
                    strokeOpacity={0.5}
                  />
                );
              })}
              {/* Edge labels (small, midpoint) */}
              {graphEdges.map((e, i) => {
                const sNode = graphNodes.find((n) => n.id === e.source);
                const tNode = graphNodes.find((n) => n.id === e.target);
                if (!sNode || !tNode) return null;
                const mx = ((sNode.x ?? 0) + (tNode.x ?? 0)) / 2;
                const my = ((sNode.y ?? 0) + (tNode.y ?? 0)) / 2;
                return (
                  <text
                    key={`el-${i}`}
                    x={mx}
                    y={my - 4}
                    textAnchor="middle"
                    fontSize={7}
                    fill="#94a3b8"
                  >
                    {e.type.replace(/_/g, " ").toLowerCase()}
                  </text>
                );
              })}
              {/* Nodes */}
              {graphNodes.map((n) => {
                const r = NODE_RADII[n.type] ?? 10;
                const color = NODE_COLORS[n.type] ?? "#94a3b8";
                const isHovered = hoveredNode?.id === n.id;
                return (
                  <g
                    key={n.id}
                    onMouseEnter={() => setHoveredNode(n)}
                    onMouseLeave={() => setHoveredNode(null)}
                    className="cursor-pointer"
                  >
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={isHovered ? r + 3 : r}
                      fill={color}
                      fillOpacity={0.85}
                      stroke={isHovered ? "#1e293b" : "white"}
                      strokeWidth={isHovered ? 2.5 : 1.5}
                    />
                    <text
                      x={n.x}
                      y={(n.y ?? 0) + r + 12}
                      textAnchor="middle"
                      fontSize={9}
                      fill="#334155"
                      fontWeight={isHovered ? 600 : 400}
                    >
                      {(n.label?.length ?? 0) > 16 ? n.label?.slice(0, 14) + "..." : n.label}
                    </text>
                  </g>
                );
              })}
            </svg>
            {/* Hover tooltip */}
            {hoveredNode && (
              <div className="absolute top-3 right-3 bg-white shadow-lg border border-slate-200 rounded-lg p-3 max-w-[220px]">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="w-3 h-3 rounded-full"
                    style={{ backgroundColor: NODE_COLORS[hoveredNode.type] ?? "#94a3b8" }}
                  />
                  <span className="text-xs font-medium text-slate-500">{hoveredNode.type}</span>
                </div>
                <p className="text-sm font-semibold text-slate-800">{hoveredNode.label}</p>
                <p className="text-xs text-slate-400 mt-0.5">{hoveredNode.id}</p>
              </div>
            )}
          </div>
        )}

        {/* Legend */}
        {!loading && graphNodes.length > 0 && (
          <div className="border-t border-slate-100 px-4 py-2 flex flex-wrap gap-4 text-xs text-slate-500">
            <span className="font-medium">Legend<InfoTip text="Each color represents a different type of biological entity in the network." />:</span>
            {Object.entries(NODE_COLORS).map(([type, color]) => (
              <span key={type} className="flex items-center gap-1">
                <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: color }} />
                {type}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Path Finder */}
      <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5">
        <h2 className="text-sm font-medium text-slate-500 mb-3">Path Finder</h2>
        <p className="text-xs text-slate-400 mb-3">
          Find all molecular paths connecting a drug to a cancer type (up to 3 hops<InfoTip text={KG_HOPS} />).
        </p>
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs text-slate-500 mb-1">Drug</label>
            <select
              value={pathDrug}
              onChange={(e) => setPathDrug(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm"
            >
              <option value="">Select drug...</option>
              {drugs.map((d) => (
                <option key={d.drugbank_id} value={d.drugbank_id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs text-slate-500 mb-1">Cancer Type</label>
            <select
              value={pathCancer}
              onChange={(e) => setPathCancer(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm"
            >
              <option value="">Select cancer...</option>
              {cancers.map((c) => (
                <option key={c.tcga_code} value={c.tcga_code}>{c.name}</option>
              ))}
            </select>
          </div>
          <button
            onClick={findPaths}
            disabled={!pathDrug || !pathCancer || pathLoading}
            className="px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 disabled:opacity-50"
          >
            {pathLoading ? "Searching..." : "Find Paths"}
          </button>
        </div>

        {paths.length > 0 && (
          <div className="mt-4 space-y-2">
            <p className="text-sm font-medium text-slate-700">{paths.length} path{paths.length !== 1 ? "s" : ""} found</p>
            {paths.slice(0, 20).map((p, i) => (
              <div key={i} className="flex items-center gap-1 text-sm py-1.5 px-3 bg-slate-50 rounded-md">
                <span className="text-xs text-slate-400 mr-2">{p.hops} hop{p.hops !== 1 ? "s" : ""}</span>
                {p.node_sequence.map((node, j) => (
                  <span key={j} className="flex items-center gap-1">
                    {j > 0 && <span className="text-slate-300 mx-1">&rarr;</span>}
                    <span
                      className="w-2 h-2 rounded-full inline-block"
                      style={{ backgroundColor: NODE_COLORS[node.labels?.[0]] ?? "#94a3b8" }}
                    />
                    <span className="text-slate-700">{node.name}</span>
                  </span>
                ))}
              </div>
            ))}
          </div>
        )}
        {!pathLoading && pathDrug && pathCancer && paths.length === 0 && (
          <p className="mt-3 text-sm text-slate-400">No paths found. Has the graph been synced?</p>
        )}
      </div>
    </div>
  );
}

function MiniStat({ label, value, tooltip }: { label: string; value: string; tooltip?: string }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-4">
      <p className="text-xs font-medium text-slate-500">
        {label}{tooltip && <InfoTip text={tooltip} />}
      </p>
      <p className="text-lg font-bold text-slate-900 mt-0.5">{value}</p>
    </div>
  );
}
