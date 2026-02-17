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
