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
  causal_dependency: number;
  gnn_link: number;
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
  total_expected: number | null;
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

// Tallula Algorithm
export interface TallulaDiscovery {
  id: number;
  hypothesis_id: number;
  drug_id: number;
  cancer_type_id: number;
  discovery_class: "robust" | "resonant" | "fragile" | "moderate" | "weak";
  deterministic_score: number;
  ubiquity: number;
  resonance: number;
  fragility_index: number;
  critical_dimension: string | null;
  score_mean: number | null;
  score_median: number | null;
  score_std: number | null;
  score_max: number | null;
  score_min: number | null;
  resonance_profile: {
    activation_dimensions: { dimension: string; activation_strength: number }[];
    narrative: string;
  } | null;
  ablation_impacts: Record<string, number> | null;
}

export interface TallulaRunSummary {
  id: number;
  created_at: string | null;
  n_lenses: number;
  n_hypotheses_input: number;
  cancer_type_id: number | null;
  n_resonant: number;
  n_robust: number;
  n_fragile: number;
  n_moderate: number;
  n_weak: number;
  parameters: Record<string, unknown>;
}

export interface CrossCancerTransfer {
  source_hypothesis_id: number;
  source_cancer_type_id: number;
  target_hypothesis_id: number;
  target_cancer_type_id: number;
  drug_id: number;
  shared_activation_dimensions: string[];
  activation_strength_in_target: number;
  source_resonance: number;
}

export interface TallulaRunResult {
  algorithm: string;
  version: string;
  run_id: number;
  parameters: {
    n_lenses: number;
    n_initial_lenses: number;
    n_adaptive_lenses: number;
    dropout_rate: number;
    dirichlet_alpha: number;
    seed: number | null;
    n_hypotheses_input: number;
    use_interactions: boolean;
    interaction_strength: number;
    adaptive_lenses: boolean;
    cross_cancer_transfer: boolean;
  };
  summary: {
    class_counts: Record<string, number>;
    n_resonant: number;
    n_robust: number;
    n_fragile: number;
    global_score_threshold_p80: number;
    n_cross_cancer_transfers: number;
  };
  discoveries: TallulaDiscovery[];
  cross_cancer_transfers: CrossCancerTransfer[];
}

// GNN Link Prediction
export interface GNNTrainingStatus {
  status: string;
  run_id: number | null;
  created_at: string | null;
  completed_at: string | null;
  epochs: number;
  graph_stats: {
    drugs: number | null;
    targets: number | null;
    cancers: number | null;
    pathways: number | null;
    positive_labels: number | null;
  };
  test_metrics: {
    roc_auc: number | null;
    avg_precision: number | null;
    accuracy: number | null;
  };
  best_val_auc: number | null;
  has_cached_predictions: boolean;
}

export interface GNNPrediction {
  drug_id: number;
  cancer_type_id: number;
  gnn_score: number;
  gnn_score_pct: number;
  source: string;
}

// Literature Monitoring
export interface LiteratureAlert {
  id: number;
  hypothesis_id: number;
  alert_type: string;
  severity: "info" | "notable" | "significant" | "critical";
  title: string;
  description: string | null;
  score_delta: number | null;
  source_pmid: string | null;
  is_read: number;
  created_at: string | null;
}

export interface ScoreHistoryEntry {
  id: number;
  old_score: number;
  new_score: number;
  delta: number;
  trigger: string;
  trigger_details: Record<string, unknown>;
  dimension_scores: DimensionScores | null;
  created_at: string | null;
}

export interface MonitoringConfig {
  id: number;
  hypothesis_id: number;
  is_active: boolean;
  alert_threshold: number;
  check_interval_hours: number;
  last_checked_at: string | null;
}

// Expression analysis
export interface TopGene {
  gene_symbol: string;
  alteration_type: string;
  frequency_percent: number | null;
  expression_zscore: number | null;
}
