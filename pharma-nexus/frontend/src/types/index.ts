export interface Drug {
  id: number;
  name: string;
  generic_name: string | null;
  pubchem_cid: number | null;
  chembl_id: string | null;
  smiles: string | null;
  molecular_formula: string | null;
  molecular_weight: number | null;
  mechanism_of_action: string | null;
  status: string;
}

export interface CancerType {
  id: number;
  name: string;
  tcga_code: string | null;
  description: string | null;
  tissue: string | null;
}

export interface Hypothesis {
  id: number;
  drug: { id: number; name: string } | null;
  cancer_type: { id: number; name: string } | null;
  composite_score: number;
  target_binding_score: number;
  pathway_overlap_score: number;
  clinical_evidence_score: number;
  evidence_summary: string | null;
  strategy: string | null;
  status: string;
  created_at: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export interface IngestionLog {
  id: number;
  source: string;
  status: string;
  records_processed: number;
  total_expected: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface HypothesisStats {
  total: number;
  average_score: number;
  by_strategy: Record<string, number>;
}
