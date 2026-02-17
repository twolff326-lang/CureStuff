export interface Drug {
  id: number;
  drugbank_id: string;
  name: string;
  generic_name: string | null;
  description: string | null;
  mechanism_of_action: string | null;
  status: string;
  indication: string | null;
}

export interface Hypothesis {
  id: number;
  drug_id: number;
  cancer_type_id: number;
  title: string;
  summary: string | null;
  composite_score: number;
  evidence_strength: string;
  status: string;
  created_at: string;
  updated_at?: string;
  dimension_scores?: {
    pathway_overlap: number;
    expression_correlation: number;
    literature_support: number;
    clinical_evidence: number;
    safety: number;
    novelty: number;
  };
  drug?: { id: number; name: string; drugbank_id: string };
  cancer_type?: { id: number; name: string; tcga_code: string };
  mechanism_narrative?: string | null;
  evidence?: HypothesisEvidence[];
  evidence_count?: number;
}

export interface HypothesisEvidence {
  id: number;
  evidence_type: string;
  source_type: string | null;
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
  average_scores: {
    composite: number;
    pathway_overlap: number;
    expression_correlation: number;
    literature_support: number;
    clinical_evidence: number;
    safety: number;
    novelty: number;
  };
  top_cancer_types: { name: string; count: number }[];
}

export interface CancerType {
  id: number;
  tcga_code: string;
  name: string;
  tissue: string | null;
  organ: string | null;
}

export interface IngestionLog {
  id: number;
  source: string;
  task_type: string;
  status: string;
  records_processed: number;
  started_at: string;
  completed_at: string | null;
}

export interface TaskProgress {
  current: number;
  total: number;
  step: string;
  detail: string;
  percent: number;
}

export interface TaskStatus {
  task_id: string;
  status: "PENDING" | "STARTED" | "PROGRESS" | "SUCCESS" | "FAILURE" | "RETRY";
  result: Record<string, unknown> | null;
  progress: TaskProgress | null;
}

export interface PaginatedResponse<T> {
  total: number;
  page: number;
  per_page: number;
  [key: string]: T[] | number;
}
