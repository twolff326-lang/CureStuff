"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi } from "@/lib/api";
import type { GraphNode, GraphEdge } from "@/types";

// ---------------------------------------------------------------
// Knowledge Graph — force-directed visualization
// ---------------------------------------------------------------

// Node type colors
const NODE_COLORS: Record<string, string> = {
  Drug: "#3b82f6",
  Target: "#10b981",
  CancerType: "#ef4444",
  Pathway: "#8b5cf6",
  Gene: "#f59e0b",
  Literature: "#6366f1",
};

const EDGE_COLORS: Record<string, string> = {
  TARGETS: "#10b981",
  MUTATED_IN: "#ef4444",
  PARTICIPATES_IN: "#8b5cf6",
  INTERACTS_WITH: "#94a3b8",
  OVEREXPRESSED_IN: "#f59e0b",
  UNDEREXPRESSED_IN: "#3b82f6",
};

interface SimNode extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx?: number | null;
  fy?: number | null;
}

export default function KnowledgeGraphPage() {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchDrug, setSearchDrug] = useState("");
  const [searchCancer, setSearchCancer] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [graphStats, setGraphStats] = useState<{
    total_nodes: number;
    total_edges: number;
    nodes: Record<string, number>;
  } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simNodesRef = useRef<SimNode[]>([]);
  const animRef = useRef<number | null>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);

  // Load graph stats
  useEffect(() => {
    fetchApi<{
      total_nodes: number;
      total_edges: number;
      nodes: Record<string, number>;
    }>("/api/knowledge-graph/stats")
      .then(setGraphStats)
      .catch(() => {});
  }, []);

  // Load subgraph
  const loadSubgraph = useCallback(
    async (type: "drug" | "cancer", query: string) => {
      if (!query.trim()) return;
      setLoading(true);
      setError(null);
      try {
        const endpoint =
          type === "drug"
            ? `/api/knowledge-graph/drug/${encodeURIComponent(query)}/neighborhood`
            : `/api/knowledge-graph/cancer/${encodeURIComponent(query)}/neighborhood`;
        const data = await fetchApi<{
          nodes: GraphNode[];
          edges: GraphEdge[];
        }>(endpoint);
        setNodes(data.nodes);
        setEdges(data.edges);
      } catch {
        setError(`No graph data found for "${query}". Try a DrugBank ID (e.g. DB00945) or TCGA code (e.g. BRCA).`);
        setNodes([]);
        setEdges([]);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Simple force simulation
  useEffect(() => {
    if (nodes.length === 0) {
      simNodesRef.current = [];
      return;
    }

    const container = canvasRef.current?.parentElement;
    const width = container?.clientWidth || 800;
    const height = Math.min(Math.max(width * 0.55, 350), 600);
    if (canvasRef.current) {
      canvasRef.current.width = width;
      canvasRef.current.height = height;
    }
    const centerX = width / 2;
    const centerY = height / 2;

    // Initialize positions
    const simNodes: SimNode[] = nodes.map((n, i) => ({
      ...n,
      x: centerX + (Math.random() - 0.5) * 300,
      y: centerY + (Math.random() - 0.5) * 300,
      vx: 0,
      vy: 0,
    }));

    const nodeMap = new Map(simNodes.map((n) => [n.id, n]));
    simNodesRef.current = simNodes;

    let iteration = 0;
    const maxIter = 200;

    const tick = () => {
      if (iteration >= maxIter) return;
      iteration++;

      const alpha = 1 - iteration / maxIter;
      const repulsion = 800 * alpha;
      const attraction = 0.02 * alpha;
      const gravity = 0.01;

      // Repulsion between all nodes
      for (let i = 0; i < simNodes.length; i++) {
        for (let j = i + 1; j < simNodes.length; j++) {
          const a = simNodes[i];
          const b = simNodes[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = repulsion / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }

      // Attraction along edges
      for (const edge of edges) {
        const a = nodeMap.get(edge.source);
        const b = nodeMap.get(edge.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = dist * attraction;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      // Gravity toward center
      for (const n of simNodes) {
        n.vx += (centerX - n.x) * gravity;
        n.vy += (centerY - n.y) * gravity;
      }

      // Apply velocity with damping
      for (const n of simNodes) {
        if (n.fx != null) {
          n.x = n.fx;
          n.vx = 0;
        } else {
          n.vx *= 0.6;
          n.x += n.vx;
        }
        if (n.fy != null) {
          n.y = n.fy;
          n.vy = 0;
        } else {
          n.vy *= 0.6;
          n.y += n.vy;
        }
        // Clamp
        n.x = Math.max(30, Math.min(width - 30, n.x));
        n.y = Math.max(30, Math.min(height - 30, n.y));
      }

      // Draw
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      ctx.clearRect(0, 0, width, height);

      // Edges
      for (const edge of edges) {
        const a = nodeMap.get(edge.source);
        const b = nodeMap.get(edge.target);
        if (!a || !b) continue;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = EDGE_COLORS[edge.type] ?? "#d1d5db";
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.4;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // Nodes
      for (const n of simNodes) {
        const radius = n.type === "Drug" || n.type === "CancerType" ? 8 : 5;
        const color = NODE_COLORS[n.type] ?? "#94a3b8";

        ctx.beginPath();
        ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();

        if (hoveredNode === n.id) {
          ctx.strokeStyle = "#1e293b";
          ctx.lineWidth = 2;
          ctx.stroke();
        }

        // Label for larger nodes
        if (radius >= 8 || hoveredNode === n.id) {
          ctx.font = "10px sans-serif";
          ctx.fillStyle = "#334155";
          ctx.textAlign = "center";
          ctx.fillText(n.label, n.x, n.y - radius - 4);
        }
      }

      if (iteration < maxIter) {
        animRef.current = requestAnimationFrame(tick);
      }
    };

    animRef.current = requestAnimationFrame(tick);

    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [nodes, edges, hoveredNode]);

  // Mouse hover detection
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    let found: string | null = null;
    for (const n of simNodesRef.current) {
      const dx = mx - n.x;
      const dy = my - n.y;
      if (dx * dx + dy * dy < 100) {
        found = n.id;
        break;
      }
    }
    setHoveredNode(found);
  };

  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 dark:text-white mb-2">
        Knowledge Graph
      </h1>
      <p className="text-slate-500 dark:text-slate-400 mb-6">
        Explore drug-target-pathway-cancer relationships.
      </p>

      {/* Graph stats */}
      {graphStats && (
        <div className="mb-4 flex flex-wrap items-center gap-4 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-5 py-3 shadow-sm">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Total Nodes
            </p>
            <p className="text-lg font-bold tabular-nums text-slate-900 dark:text-white">
              {graphStats.total_nodes.toLocaleString()}
            </p>
          </div>
          <div className="hidden h-8 w-px bg-slate-200 sm:block" />
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Total Edges
            </p>
            <p className="text-lg font-bold tabular-nums text-slate-900 dark:text-white">
              {graphStats.total_edges.toLocaleString()}
            </p>
          </div>
          <div className="hidden h-8 w-px bg-slate-200 sm:block" />
          {Object.entries(graphStats.nodes || {}).map(([type, count]) => (
            <div key={type}>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                {type}
              </p>
              <p className="text-lg font-bold tabular-nums text-slate-900 dark:text-white">
                {count.toLocaleString()}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Search controls */}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">
            Drug (DrugBank ID)
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={searchDrug}
              onChange={(e) => setSearchDrug(e.target.value.toUpperCase())}
              placeholder="e.g. DB00945"
              className="w-36 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
            />
            <button
              onClick={() => loadSubgraph("drug", searchDrug)}
              disabled={loading || !searchDrug}
              className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Load
            </button>
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500 mb-1">
            Cancer (TCGA code)
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={searchCancer}
              onChange={(e) => setSearchCancer(e.target.value.toUpperCase())}
              placeholder="e.g. BRCA"
              className="w-36 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
            />
            <button
              onClick={() => loadSubgraph("cancer", searchCancer)}
              disabled={loading || !searchCancer}
              className="rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              Load
            </button>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700">
          {error}
        </div>
      )}

      {/* Canvas */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-sm overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <span className="inline-block h-6 w-6 animate-spin rounded-full border-4 border-slate-200 border-t-blue-600 mr-2" />
            <span className="text-sm text-slate-500">Loading graph...</span>
          </div>
        ) : nodes.length === 0 ? (
          <div className="py-20 text-center text-sm text-slate-400">
            Enter a DrugBank ID or TCGA cancer code above to explore the graph.
          </div>
        ) : (
          <div className="relative">
            <canvas
              ref={canvasRef}
              width={800}
              height={500}
              onMouseMove={handleMouseMove}
              className="w-full cursor-crosshair"
            />
            {/* Legend */}
            <div className="absolute bottom-3 right-3 rounded-md bg-white/90 border border-slate-200 px-3 py-2 text-xs">
              <p className="font-medium text-slate-600 mb-1">Node Types</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                {Object.entries(NODE_COLORS).map(([type, color]) => (
                  <div key={type} className="flex items-center gap-1.5">
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                    <span className="text-slate-500">{type}</span>
                  </div>
                ))}
              </div>
            </div>
            {/* Node count */}
            <div className="absolute top-3 left-3 rounded-md bg-white/90 border border-slate-200 px-3 py-1.5 text-xs text-slate-500">
              {nodes.length} nodes, {edges.length} edges
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
