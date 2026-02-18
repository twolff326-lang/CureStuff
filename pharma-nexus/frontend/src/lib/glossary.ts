/**
 * Plain-English definitions for every metric, score, and scientific
 * term shown in the UI. Written so a non-scientist can understand.
 */

// ── Scores ──────────────────────────────────────────────────────────

export const COMPOSITE_SCORE =
  "An overall score from 0-100 that combines six types of evidence " +
  "(pathway connections, gene expression, published research, clinical " +
  "trials, safety data, and novelty). Higher = stronger hypothesis. " +
  "70+ is strong, 50-69 is moderate, 30-49 is suggestive, below 30 is speculative.";

export const ADJUSTED_SCORE =
  "The composite score after an AI review. If an AI model found " +
  "the hypothesis convincing, the score stays high. If the AI found " +
  "problems, the score is reduced. This prevents over-optimistic rankings.";

export const NOVELTY_SCORE =
  "How new and unstudied this drug-cancer combination is. " +
  "A high score means few or no researchers have explored this connection " +
  "before — it's a genuinely original finding. A low score means it's " +
  "already well-known in the scientific literature.";

export const LLM_CONFIDENCE =
  "An AI model's independent assessment of how likely this hypothesis " +
  "is to be correct, on a scale of 0-100. The AI reads the evidence and " +
  "gives its own judgment — separate from the composite score. " +
  "Think of it as a second opinion from an AI reviewer.";

export const MIN_SCORE_THRESHOLD =
  "Only show or process hypotheses scoring above this number (0-100). " +
  "Setting this to 30 filters out the weakest hypotheses. " +
  "Most useful results are above 40.";

export const CONFIDENCE_PERCENT =
  "How confident the system is in this specific piece of evidence, " +
  "expressed as a percentage. 80%+ is high confidence; below 50% means " +
  "the connection is uncertain.";

// ── Evidence Strength ───────────────────────────────────────────────

export const EVIDENCE_STRENGTH =
  "Overall quality rating based on how much supporting evidence exists. " +
  "Strong: multiple independent evidence types confirm it. " +
  "Moderate: solid evidence from some sources. " +
  "Suggestive: some evidence, but gaps remain. " +
  "Speculative: limited evidence — needs more investigation.";

export const STRENGTH_STRONG =
  "Multiple independent lines of evidence support this hypothesis — " +
  "pathway data, gene expression, and published papers all agree.";

export const STRENGTH_MODERATE =
  "Good supporting evidence from several sources, but not all evidence " +
  "types have been confirmed independently.";

export const STRENGTH_SUGGESTIVE =
  "Some evidence points toward this connection, but significant gaps " +
  "remain. More data would strengthen or refute it.";

export const STRENGTH_SPECULATIVE =
  "Very early stage — limited evidence available. This is an educated " +
  "guess that needs substantial further investigation.";

// ── Evidence Dimensions ─────────────────────────────────────────────

export const DIM_PATHWAY_OVERLAP =
  "How many biological signaling pathways are shared between the drug's " +
  "targets and the cancer's affected genes. Higher overlap means the drug " +
  "acts on pathways that are important in this cancer.";

export const DIM_EXPRESSION_CORRELATION =
  "Whether the genes targeted by this drug are abnormally active " +
  "(overexpressed or underexpressed) in this cancer. A high score means " +
  "the drug targets genes that are clearly dysregulated in tumor cells.";

export const DIM_LITERATURE_SUPPORT =
  "How many published research papers mention both this drug and cancer " +
  "type together. More papers = more existing research supporting the " +
  "connection, but too many may mean it's already well-known.";

export const DIM_CLINICAL_EVIDENCE =
  "Whether any clinical trials have tested this drug for this cancer. " +
  "Active Phase III trials score highest. No trials scores zero — " +
  "which is expected for novel discoveries.";

export const DIM_SAFETY =
  "The drug's safety profile based on its regulatory status. " +
  "FDA-approved drugs score highest (known side effects), while " +
  "experimental compounds score lower (less safety data available).";

export const DIM_NOVELTY =
  "How original this drug-cancer combination is. Score decreases as " +
  "more existing research is found. A high novelty score means this " +
  "is a genuinely new connection that hasn't been widely studied.";

export const DIMENSION_TOOLTIPS: Record<string, string> = {
  pathway_overlap: DIM_PATHWAY_OVERLAP,
  expression_correlation: DIM_EXPRESSION_CORRELATION,
  literature_support: DIM_LITERATURE_SUPPORT,
  clinical_evidence: DIM_CLINICAL_EVIDENCE,
  safety: DIM_SAFETY,
  novelty: DIM_NOVELTY,
};

// ── Evidence Types ──────────────────────────────────────────────────

export const EVIDENCE_TYPE_TOOLTIPS: Record<string, string> = {
  pathway_overlap:
    "This drug and cancer share biological pathways — the molecular " +
    "signaling routes that control cell behavior.",
  expression_correlation:
    "Genes targeted by this drug show abnormal activity levels in this " +
    "cancer's tumor cells.",
  literature_support:
    "Published research papers mention both this drug and cancer, " +
    "suggesting scientists have observed a connection.",
  clinical_evidence:
    "Clinical trials (tests in human patients) exist for this drug " +
    "in this cancer type.",
  safety:
    "Evidence about the drug's side effects and safety from regulatory " +
    "data (e.g., FDA approval status).",
  target_mutation:
    "A gene targeted by this drug is frequently mutated (changed) in " +
    "this cancer, making it a promising drug target.",
  literature_co_mention:
    "A research paper mentions both this drug and cancer in the same " +
    "abstract — indirect but potentially meaningful.",
};

// ── LLM Recommendations ────────────────────────────────────────────

export const REC_PROCEED =
  "Strong evidence from multiple sources. This hypothesis is ready " +
  "for laboratory validation experiments.";

export const REC_CAUTION =
  "Promising evidence, but some uncertainties remain. Worth pursuing " +
  "with additional verification steps.";

export const REC_MORE_DATA =
  "Interesting lead, but key evidence is missing. Gather more data " +
  "before committing resources to this hypothesis.";

export const REC_DEPRIORITIZE =
  "Weak evidence or significant concerns identified. Other hypotheses " +
  "are more promising — revisit if new data emerges.";

export const RECOMMENDATION_TOOLTIPS: Record<string, string> = {
  proceed_immediately: REC_PROCEED,
  proceed_with_caution: REC_CAUTION,
  additional_data_needed: REC_MORE_DATA,
  deprioritize: REC_DEPRIORITIZE,
};

// ── Discovery proposals ─────────────────────────────────────────────

export const PROPOSAL_CONFIDENCE =
  "The AI model's confidence (0-100%) that this drug-cancer connection " +
  "is real and worth investigating. Based on the reasoning chain the " +
  "AI constructed from published papers and drug data.";

export const REASONING_CHAIN =
  "The logical steps the AI used to connect this drug to this cancer. " +
  "Each step represents a known biological relationship, chained " +
  "together to form a novel hypothesis. Example: Drug blocks Protein A → " +
  "Protein A activates Pathway B → Pathway B drives Cancer C.";

export const PROPOSAL_NOVELTY =
  "Why the AI believes this connection hasn't been discovered before. " +
  "It identified the link by reading across multiple papers that " +
  "individually never made this connection.";

export const PROPOSAL_STATUS_TOOLTIPS: Record<string, string> = {
  proposed:
    "Newly generated by the AI. Awaiting review or promotion to a " +
    "full scored hypothesis.",
  hypothesis_created:
    "Promoted into the hypothesis scoring pipeline, where it gets " +
    "scored across six evidence dimensions.",
  rejected:
    "Reviewed and determined to be not worth pursuing — either " +
    "already known, implausible, or poorly supported.",
};

// ── Analysis page ───────────────────────────────────────────────────

export const COST_MODE =
  "Controls which AI model is used and how much each analysis costs. " +
  "Economy: fastest and cheapest, uses a smaller model. " +
  "Standard: balanced speed and quality. " +
  "Premium: most thorough analysis using the most capable model.";

export const COST_MODE_ECONOMY =
  "Uses the fastest, most affordable AI model (Haiku). Best for " +
  "initial screening of many hypotheses. ~$0.01 per hypothesis.";

export const COST_MODE_STANDARD =
  "Uses a mid-tier model (Sonnet) for most work, with the top model " +
  "(Opus) for the most promising cases. ~$0.04 per hypothesis.";

export const COST_MODE_PREMIUM =
  "Uses the most capable AI model (Opus) for everything. Most thorough " +
  "analysis but slowest and most expensive. ~$0.19 per hypothesis.";

// ── Knowledge graph ─────────────────────────────────────────────────

export const KG_NODES =
  "Entities in the biological network — drugs, proteins, pathways, " +
  "genes, cancers, papers, and mutations. Each colored dot represents " +
  "one entity.";

export const KG_EDGES =
  "Connections between entities — for example, 'Drug X targets Protein Y' " +
  "or 'Gene Z is mutated in Cancer W'. Lines show how biological " +
  "entities are related to each other.";

export const KG_NODE_TYPES =
  "The different categories of biological entities in the graph. " +
  "Each type is shown in a different color.";

export const KG_EDGE_TYPES =
  "The different types of biological relationships between entities — " +
  "targeting, participating in pathways, being mutated, etc.";

export const KG_HOPS =
  "How many steps (connections) to follow between two entities. " +
  "1 hop = directly connected. 2 hops = connected through one " +
  "intermediate entity. 3 hops = two intermediates.";

export const EDGE_TYPE_TOOLTIPS: Record<string, string> = {
  TARGETS:
    "This drug binds to and acts on this protein target.",
  PARTICIPATES_IN:
    "This protein or gene is part of this biological pathway.",
  IS_TARGET:
    "This gene/protein is a known drug target.",
  MUTATED_IN:
    "This gene is frequently mutated (has DNA changes) in this cancer.",
  OVEREXPRESSED_IN:
    "This gene is abnormally active (produces too much protein) in this cancer.",
  AMPLIFIED_IN:
    "This gene has extra copies in this cancer's DNA, often causing overactivity.",
  DELETED_IN:
    "This gene is missing or reduced in this cancer's DNA.",
  INTERACTS_WITH:
    "These two proteins physically bind to or influence each other.",
  ASSOCIATED_WITH:
    "A statistical or experimental association exists between these entities.",
  MENTIONS_DRUG:
    "This research paper discusses this drug.",
  STUDIES_CANCER:
    "This research paper studies this cancer type.",
};

// ── Data sources ────────────────────────────────────────────────────

export const RECORDS_PROCESSED =
  "The number of individual data entries imported from this database. " +
  "For drug databases, each record is typically one drug. For genomics " +
  "databases, each record may be one mutation or gene expression measurement.";

// ── Reports page ────────────────────────────────────────────────────

export const ANALYSIS_PIPELINE =
  "Six AI-powered analyses that examine a hypothesis from different " +
  "angles: mechanistic explanation, critical review, literature summary, " +
  "experiment design, confidence assessment, and comparison to similar cases.";

export const DRUG_PORTFOLIO_REPORT =
  "A comprehensive report for one drug across ALL cancer types it could " +
  "target. Shows ranked cancer targets, molecular targets, tissue distribution, " +
  "and recommendations for which cancers to pursue.";

export const COMPARATIVE_REPORT =
  "A side-by-side comparison of multiple hypotheses showing score dimensions, " +
  "ranking, and individual summaries. Useful for deciding which candidates " +
  "to prioritize for validation.";

export const BATCH_REPORTS =
  "Generate all report types at once: executive summary, novel discoveries, " +
  "all cancer summaries, and top 50 hypothesis reports. This runs in the " +
  "background and can take several minutes.";

export const ANALYSIS_TYPE_TOOLTIPS: Record<string, string> = {
  narrative:
    "An AI-written explanation of the biological mechanism — how this " +
    "drug might work against this cancer, in plain language.",
  critique:
    "A critical review that identifies weaknesses, gaps, and risks " +
    "in the hypothesis — like a peer review of a research paper.",
  literature_synthesis:
    "A summary of the published research papers that support or " +
    "contradict this hypothesis.",
  experiment_design:
    "A suggested plan for laboratory experiments to test this " +
    "hypothesis — from cell cultures to potential clinical trials.",
  confidence:
    "An AI confidence assessment with analysis of similar past " +
    "drug repurposing cases to calibrate expectations.",
  comparative:
    "A comparison of this hypothesis against similar drug-cancer " +
    "hypotheses to highlight what makes this one unique or risky.",
};
