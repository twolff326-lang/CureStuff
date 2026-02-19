// ---------------------------------------------------------------
// Core domain types matching backend API responses
// ---------------------------------------------------------------

export interface Drug {
  id: number;
  drugbank_id: string;
  name: string;
  generic_name: string | null;
  description: string | null;
  mechanism_of_action: string | null;
  status: string;
  indication: string | null;
  molecular_formula: string | null;
  inchi_key: string | null;
}

export interface DimensionScores {
  pathway_overlap: number;
  expression_correlation: number;
  literature_support: number;
  clinical_evidence: number;
  safety: number;
  novelty: number;
}

export interface Hypothesis {
  id: number;
  drug_id: number;
  cancer_type_id: number;
  title: string;
  summary: string | null;
  composite_score: number;
  evidence_strength: string;
  dimension_scores: DimensionScores;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  mechanism_narrative?: string | null;
  reviewer_notes?: string | null;
  drug?: { id: number; name: string | null; drugbank_id: string | null };
  cancer_type?: { id: number; name: string | null; tcga_code: string | null };
  evidence?: HypothesisEvidence[];
  evidence_count?: number;
}

export interface HypothesisEvidence {
  id: number;
  evidence_type: string;
  source_type: string;
  source_id: string | null;
  description: string | null;
  strength: string;
  confidence: number;
  raw_data: Record<string, unknown> | null;
}

export interface HypothesisStats {
  total_hypotheses: number;
  by_evidence_strength: Record<string, number>;
  by_status: Record<string, number>;
  average_scores: DimensionScores & { composite: number };
  top_cancer_types: { name: string; count: number }[];
}

export interface CancerType {
  id: number;
  tcga_code: string;
  name: string;
  tissue: string | null;
  organ: string | null;
  subtype?: string | null;
  sample_count?: number | null;
  mutation_count?: number;
}

export interface IngestionLog {
  id: number;
  source: string;
  task_type: string;
  status: string;
  records_processed: number;
  errors?: unknown[] | null;
  started_at: string;
  completed_at: string | null;
}

export interface LLMAnalysis {
  id: number;
  analysis_type: string;
  model_used: string;
  content: Record<string, unknown> | string;
  input_tokens: number;
  output_tokens: number;
  generation_time_seconds: number;
  created_at: string | null;
  updated_at: string | null;
}

// Knowledge graph
export interface GraphNode {
  id: string;
  type: string;
  label: string;
  pg_id?: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  properties?: Record<string, unknown>;
}

// Expression analysis
export interface TopGene {
  gene_symbol: string;
  alteration_type: string;
  frequency_percent: number | null;
  expression_zscore: number | null;
}
