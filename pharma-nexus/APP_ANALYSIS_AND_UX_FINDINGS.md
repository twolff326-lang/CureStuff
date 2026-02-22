# Pharma-Nexus: Full Application Analysis & UX Findings

**Date:** 2026-02-22
**Scope:** End-to-end architecture review, user experience audit, mock discovery findings, and LLM output examples

---

## 1. Application Architecture Overview

### Stack
| Layer | Technology | Purpose |
|-------|-----------|---------|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS, Recharts | Interactive dashboard & visualization |
| Backend | FastAPI, SQLAlchemy (async), Celery + Redis | REST API, async task orchestration |
| Database | PostgreSQL + pgvector | Relational data + vector similarity search |
| Graph DB | Neo4j | Knowledge graph (drug-target-pathway-cancer) |
| LLM | Claude (Sonnet for bulk, Opus for top hypotheses) | Mechanistic narratives, critiques, experiment design |
| Queue | Redis + Celery | Background ingestion & pipeline tasks |

### Data Flow
```
External Sources (PubChem, ChEMBL, TCGA, DepMap, PubMed, etc.)
        |
        v
  [Ingestion Connectors]  (16 connectors: pubchem, chembl, tcga, depmap, etc.)
        |
        v
  [PostgreSQL + Neo4j]  (drugs, targets, pathways, mutations, literature, trials)
        |
        v
  [Hypothesis Engine]  (6 discovery strategies x 11 scoring dimensions)
        |
        v
  [Evidence Scorer]  (Fisher's exact, bootstrap CIs, pharmacological modeling)
        |
        v
  [LLM Analyst]  (Claude API: narrative, critique, lit synthesis, experiments, confidence)
        |
        v
  [Frontend Dashboard]  (9 pages: dashboard, hypotheses, analysis, pipeline, tallula, etc.)
```

### Pages & Navigation
| Page | Route | Purpose |
|------|-------|---------|
| Dashboard | `/` | Live stats, top hypotheses, evidence distribution, recent ingestion |
| Hypotheses | `/hypotheses` | Searchable/filterable table of all hypotheses with dimension bars |
| Hypothesis Detail | `/hypotheses/[id]` | Radar chart, dimension scores, evidence, LLM narrative tabs |
| Combinations | `/combinations` | Drug combination synergy analysis |
| Tallula | `/tallula` | Stochastic resonance ensemble discovery algorithm |
| Knowledge Graph | `/knowledge-graph` | Interactive graph visualization |
| Run Pipeline | `/pipeline` | One-click multi-phase pipeline with live progress |
| Analysis | `/analysis` | Expression analysis, volcano plots, pipeline triggers |
| Data Sources | `/data-sources` | Ingestion connector status & controls |
| Ingested Data | `/ingested-data` | Browse indexed drugs, targets, pathways |

---

## 2. Most Impactful UX Findings

### FINDING 1: No Loading Skeleton on Dashboard Cold Start
**Severity:** Medium | **Impact:** First impression / perceived performance
**Location:** `frontend/src/app/page.tsx:32-58`

When the dashboard loads for the first time, all four `StatCard` components and the `MiniStat` row render with em-dash placeholders (`"---"`) while 4 parallel API calls resolve. There is no skeleton/shimmer state, making the page look broken for the 1-3 seconds before data arrives.

**Current behavior:**
- User sees the page title, then a grid of cards all showing `---`
- No spinner, no skeleton, no indication that data is loading
- The `recentLogs` table shows "No ingestion runs yet" before data arrives (a false empty state)

**Recommendation:**
Add a `loading` state with skeleton pulse animations for the stat cards and table rows. The dashboard already uses `Promise.allSettled` which is correct, but the UI should distinguish "loading" from "empty."

---

### FINDING 2: Hypothesis Detail LLM Analysis Renders Raw JSON
**Severity:** High | **Impact:** Core value proposition degraded
**Location:** `frontend/src/app/hypotheses/[id]/page.tsx:400-434`

The `AnalysisPanel` component that renders LLM-generated narratives, critiques, and confidence assessments falls back to a raw `<pre>{JSON.stringify(content, null, 2)}</pre>` block when the content is not a plain string. Since the LLM analyst returns structured JSON objects (with keys like `molecular_mechanism`, `pathway_analysis`, `mechanistic_weaknesses`, etc.), users see raw JSON instead of a formatted, readable analysis.

**Current behavior:**
```
{
  "title": "Metformin as a Therapeutic...",
  "summary": "Metformin, an AMPK activator...",
  "molecular_mechanism": "The primary mechanism...",
  ...
}
```

**Recommendation:**
Build typed renderers for each analysis type:
- **Narrative:** Render `title`, `summary`, `molecular_mechanism`, `pathway_analysis`, `expression_context`, `clinical_relevance` as headed prose sections with the `key_genes` and `key_pathways` as tag chips.
- **Critique:** Render `mechanistic_weaknesses` as a severity-tagged card list, `evidence_gaps` as a checklist, `kill_criteria` as red-highlighted items.
- **Confidence:** Render `probability_of_success` as a funnel chart (preclinical -> phase1 -> phase2 -> overall), `dimension_assessments` as a scored breakdown.

---

### FINDING 3: Pipeline Phase Errors Are Truncated
**Severity:** Medium | **Impact:** Debugging / operator experience
**Location:** `frontend/src/app/pipeline/page.tsx:321-325`

When a pipeline phase fails, the error message is rendered with `truncate` CSS class in a single line. Long error messages (e.g., Python tracebacks or API timeout messages) are invisible to the operator.

**Current behavior:**
```tsx
{phase.error && (
  <p className="text-xs text-red-600 dark:text-red-400 mt-0.5 truncate">
    {phase.error}
  </p>
)}
```

**Recommendation:**
Show the first line truncated by default with a "Show full error" expand toggle. Wrap the full error in a `<pre>` block with a max-height and scroll.

---

### FINDING 4: Analysis Page Pipeline Triggers Have No Confirmation
**Severity:** Medium | **Impact:** Accidental expensive operations
**Location:** `frontend/src/app/analysis/page.tsx:61-77`

The three pipeline trigger buttons ("Run Expression Analysis", "Generate Hypotheses", "LLM Narratives") fire immediately on click with no confirmation dialog. The "LLM Narratives" button triggers `llm_full_analysis` which calls the Claude API for every qualifying hypothesis -- potentially hundreds of API calls costing real money.

**Recommendation:**
Add a confirmation modal for destructive/expensive operations, especially "LLM Narratives" which should show estimated cost (based on hypothesis count * average tokens * model pricing from `PRICING` dict in `llm_analyst.py`).

---

### FINDING 5: Tallula Discovery Cards Show Hypothesis IDs, Not Titles
**Severity:** Medium | **Impact:** Discoverability / context
**Location:** `frontend/src/app/tallula/page.tsx:515-517`

Tallula discovery cards display `Hypothesis #{discovery.hypothesis_id}` instead of the hypothesis title (e.g., "Metformin for Pancreatic Cancer"). The user must mentally map IDs to hypotheses or navigate away to the hypotheses page.

**Recommendation:**
Include the hypothesis title in the `TallulaDiscovery` API response and display it in the card header alongside the ID.

---

### FINDING 6: Dashboard Polls Every 30 Seconds Unconditionally
**Severity:** Low | **Impact:** Battery / bandwidth on idle tabs
**Location:** `frontend/src/app/page.tsx:56-58`

The dashboard sets a 30-second `setInterval` that fires all 4 API calls regardless of whether the browser tab is visible. This wastes bandwidth and backend resources when the tab is backgrounded.

**Recommendation:**
Use `document.visibilitychange` to pause polling when the tab is hidden. Consider using `react-query` or `SWR` which handle this natively.

---

### FINDING 7: No Error Boundary on Hypothesis Detail
**Severity:** Low | **Impact:** Resilience
**Location:** `frontend/src/app/hypotheses/[id]/page.tsx`

If the hypothesis API returns malformed dimension scores or the LLM analysis content has unexpected structure, the Recharts `RadarChart` or `AnalysisPanel` can throw and crash the entire page. There is a global `error.tsx` but no page-level error boundary.

---

## 3. Mock Findings: Most Impactful Drug Repurposing Hypotheses

Below are realistic mock examples of what the system would surface after full data ingestion and hypothesis generation. These represent the types of high-scoring discoveries across the 11 evidence dimensions.

### Mock Hypothesis 1: Metformin for Pancreatic Ductal Adenocarcinoma (PDAC)
| Dimension | Score | Key Evidence |
|-----------|-------|-------------|
| Composite | **72/100** | Strong |
| Pathway Overlap | 81 | AMPK/mTOR pathway shared; drug targets PRKAB1 in PI3K/AKT/mTOR cascade (FDR-adj p=3.2e-04, OR=4.7) |
| Expression Correlation | 65 | PRKAB1 overexpressed in PDAC (z=3.1); metformin is AMPK activator = therapeutically aligned |
| Literature Support | 58 | 12 co-mention papers; 2 clinical trials (Phase 2); quality-weighted score 58 |
| Clinical Evidence | 45 | NCT02005419 (Phase 2, completed, n=120); NCT01971034 (Phase 1, active) |
| Safety | 85 | FDA-approved, known MoA, existing diabetes indication, extensive safety data |
| Novelty | 32 | Well-explored (low novelty) -- multiple papers and trials exist |
| Causal Dependency | 61 | PRKAB1 essential in pancreatic lineage (gene_effect=-0.72, dep_probability=0.81, SELECTIVE) |
| Mutation Context | 48 | KRAS mutation (freq=92%) shares RAS/MAPK pathway with AMPK targets; conditional dependency |
| Polypharmacology | 35 | Off-target activity against OCT1 transporter (overexpressed in PDAC, z=2.8) |
| Drug Sensitivity | 0 | Awaiting PRISM/GDSC ingestion |

**Discovery Strategies:** direct_target, pathway_mediated, literature_seeded, expression_driven

---

### Mock Hypothesis 2: Disulfiram for Glioblastoma Multiforme (GBM)
| Dimension | Score | Key Evidence |
|-----------|-------|-------------|
| Composite | **64/100** | Moderate |
| Pathway Overlap | 55 | ALDH1A1 in retinol metabolism + NRF2/oxidative stress pathways (FDR-adj p=0.008) |
| Expression Correlation | 72 | ALDH1A1 overexpressed in GBM stem cells (z=4.2); disulfiram is ALDH inhibitor |
| Literature Support | 43 | 7 papers; 1 preclinical showing BBB penetration of copper-disulfiram complex |
| Clinical Evidence | 22 | NCT02678975 (Phase 2, terminated, n=40 -- enrolled but low accrual) |
| Safety | 75 | FDA-approved (alcoholism), known MoA, well-characterized pharmacology |
| Novelty | 55 | Moderate novelty -- some exploration but not exhaustive |
| Causal Dependency | 47 | ALDH1A1 moderately essential in CNS lineage (gene_effect=-0.58, dep_probability=0.64) |
| Mutation Context | 58 | TP53 mutation (freq=28%) + ALDH1A1 in oxidative stress pathway = conditional vulnerability |
| Polypharmacology | 52 | Off-target bioassay activity against GSTP1 (glutathione transferase, overexpressed z=3.5 in GBM) |
| Drug Sensitivity | 0 | Awaiting PRISM/GDSC ingestion |

**Discovery Strategies:** direct_target, expression_driven, interaction_network

---

### Mock Hypothesis 3: Propranolol for Angiosarcoma
| Dimension | Score | Key Evidence |
|-----------|-------|-------------|
| Composite | **58/100** | Moderate |
| Pathway Overlap | 42 | ADRB2 in VEGF/angiogenesis signaling (Fisher p=0.02, OR=2.8) |
| Expression Correlation | 68 | ADRB2 overexpressed in angiosarcoma (z=3.8); propranolol = beta-blocker (inhibitor) |
| Literature Support | 38 | 5 case reports + 1 retrospective cohort showing responses |
| Clinical Evidence | 15 | No registered trials for angiosarcoma; trials exist for infantile hemangioma |
| Safety | 80 | FDA-approved, decades of clinical use, well-known side effects |
| Novelty | 78 | High novelty -- very few papers on propranolol + angiosarcoma specifically |
| Causal Dependency | 33 | ADRB2 weakly essential in bone lineage (gene_effect=-0.38, below -0.5 threshold) |
| Mutation Context | 25 | KDR mutation (freq=12%) in VEGF pathway, weak link to ADRB2 signaling |
| Polypharmacology | 28 | Off-target activity against SRC kinase (relevant to sarcoma biology) |
| Drug Sensitivity | 0 | Awaiting PRISM/GDSC ingestion |

**Discovery Strategies:** expression_driven, pathway_mediated, literature_seeded

---

### Mock Tallula Algorithm Output
The Tallula Stochastic Resonance Ensemble would classify these hypotheses as:

| Hypothesis | Deterministic Score | Tallula Class | Resonance | Ubiquity | Fragility |
|-----------|-------------------|---------------|-----------|----------|-----------|
| Metformin + PDAC | 72 | **Robust** | 8.3 | 0.89 | 0.12 |
| Disulfiram + GBM | 64 | **Resonant** | 14.2 | 0.71 | 0.31 |
| Propranolol + Angiosarcoma | 58 | **Moderate** | 6.1 | 0.52 | 0.44 |

Disulfiram + GBM is classified as "Resonant" -- meaning stochastic perturbation of scoring weights causes its score to spike significantly in certain configurations, suggesting hidden signal that the deterministic scorer underweights. The critical dimension driving resonance is `polypharmacology` (off-target GSTP1 activity becomes dominant when oxidative stress pathways are upweighted).

---

## 4. Example LLM Output: Full Mechanistic Narrative

Below is an example of what the Claude-generated narrative would look like for the top hypothesis (Metformin for PDAC). This is produced by `LLMAnalyst.generate_narrative()` using Claude Opus (since composite score >= 70).

### Request Context Sent to Claude
- Drug: Metformin (DB00331), approved, AMPK activator
- Cancer: Pancreatic Ductal Adenocarcinoma (PAAD)
- 3 drug targets with binding affinities
- 8 relevant pathways (PI3K/AKT/mTOR, AMPK signaling, etc.)
- Top 10 cancer mutations (KRAS 92%, TP53 72%, CDKN2A 31%, SMAD4 21%)
- 10 molecular expression profiles
- 12 supporting papers with extracted findings
- 2 clinical trials

### Claude Output (Structured JSON, rendered as prose)

---

#### Metformin as a Metabolic Intervention for Pancreatic Ductal Adenocarcinoma: Mechanistic Rationale

**Summary:**
Metformin, a first-line antidiabetic biguanide and AMPK activator, shows multi-layered mechanistic rationale for repurposing against pancreatic ductal adenocarcinoma (PDAC). Its primary mechanism involves AMPK-mediated inhibition of mTORC1 signaling, which is constitutively activated in >80% of PDAC tumors downstream of KRAS mutations. Secondary effects on cancer metabolism, tumor microenvironment remodeling, and cancer stem cell targeting provide complementary therapeutic vectors.

**Molecular Mechanism:**

The mechanistic basis for metformin in PDAC operates through three interconnected biological axes:

**1. AMPK/mTOR Axis Disruption.** Metformin activates AMP-activated protein kinase (AMPK) through inhibition of mitochondrial Complex I, increasing the AMP:ATP ratio. In PDAC, this is therapeutically significant because KRAS mutations (present in ~92% of cases) constitutively activate the PI3K/AKT/mTOR cascade. AMPK activation by metformin directly phosphorylates TSC2 (tuberin) and Raptor, inhibiting mTORC1 -- effectively counteracting the KRAS-driven proliferative signal from a parallel metabolic control point. This represents a pathway-orthogonal strategy: rather than targeting KRAS directly (which has proven challenging), metformin attacks a downstream effector from an independent regulatory node.

**2. Metabolic Vulnerability Exploitation.** PDAC exhibits a distinctive metabolic phenotype characterized by rewired glucose metabolism, increased dependence on autophagy, and altered lipid synthesis. Metformin's inhibition of hepatic gluconeogenesis and reduction of circulating insulin levels addresses the well-documented association between hyperinsulinemia and PDAC progression. At the cellular level, AMPK activation suppresses acetyl-CoA carboxylase (ACC), reducing de novo lipogenesis that PDAC cells rely upon for membrane biosynthesis during rapid proliferation. The expression data supports this: PRKAB1 (AMPK beta-1 subunit) shows a z-score of 3.1 (overexpressed) in PDAC tissue, suggesting the cancer may be upregulating AMPK machinery for its own metabolic adaptation -- creating a paradoxical vulnerability when the pathway is pharmacologically hijacked by metformin.

**3. Tumor Microenvironment Effects.** PDAC is characterized by dense desmoplastic stroma that constitutes up to 80% of tumor mass. Emerging evidence suggests metformin modulates cancer-associated fibroblast (CAF) activity through AMPK-dependent NF-kB suppression, potentially reducing the stromal barrier to drug penetration. Additionally, metformin-mediated reduction of circulating insulin and IGF-1 may attenuate paracrine survival signaling from the microenvironment.

**Pathway Analysis:**

The drug-cancer pathway intersection is statistically significant. Fisher's exact test for shared pathway enrichment between metformin's targets and PDAC-altered genes yields FDR-adjusted p=3.2e-04 with an odds ratio of 4.7 across the PI3K/AKT/mTOR, AMPK signaling, and insulin signaling pathways. The overlap is not merely correlative: 3 of metformin's 4 known targets (PRKAB1, PRKAA1, PRKAA2) are direct members of pathways containing PDAC driver genes (KRAS, PIK3CA, STK11). This shared pathway membership was confirmed by hypergeometric enrichment test (p=1.8e-03).

The causal dependency data from DepMap CRISPR screens strengthens this connection: PRKAB1 shows a gene effect of -0.72 in pancreatic cancer cell lines (classified as "essential"), with a dependency probability of 0.81 and strong selectivity -- meaning pancreatic cancer cells are more dependent on PRKAB1 than most other lineages. This is consistent with PDAC's heightened dependence on metabolic rewiring.

**Expression Context:**

Gene expression analysis reveals therapeutically favorable alignment. PRKAB1 overexpression (z-score=3.1) combined with metformin's mechanism as an AMPK activator creates a pharmacologically coherent scenario: the target pathway is already upregulated (creating potential oncogenic dependency), and the drug modulates it in a therapeutically rational direction. The expression scoring yields a compatibility of 0.78 (action_match=1.0 * expression_magnitude=0.78 * binding_potency=1.0).

**Clinical Relevance:**

Two clinical trials provide translational context:
- **NCT02005419** (Phase 2, completed, n=120): Metformin + gemcitabine in advanced PDAC. While the primary endpoint (PFS) did not reach statistical significance in the ITT population, a pre-specified subgroup analysis of patients with BMI>25 showed a trend toward improved overall survival (HR=0.72, 95% CI 0.48-1.08).
- **NCT01971034** (Phase 1, active): Metformin dose-escalation with nab-paclitaxel.

The safety profile is exceptional for drug repurposing: metformin has >60 years of clinical use, well-characterized pharmacokinetics, a benign side-effect profile (primarily GI), and costs <$0.10/day. The regulatory path for repurposing an approved generic is substantially shorter than de novo development.

**Key Genes:** PRKAB1, PRKAA1, PRKAA2, KRAS, TP53, PIK3CA, STK11, TSC2, MTOR
**Key Pathways:** PI3K/AKT/mTOR signaling, AMPK signaling, Insulin signaling, Autophagy regulation
**Confidence Note:** Moderate-to-high confidence. The mechanistic rationale is strong and multi-layered, but the Phase 2 clinical data is mixed. The hypothesis would benefit from biomarker-stratified trial design (e.g., KRAS G12D + high BMI subgroup) rather than unselected patient populations.

---

### Claude Output: Devil's Advocate Critique (for same hypothesis)

```json
{
  "overall_assessment": "cautious",
  "summary": "While the AMPK/mTOR mechanistic rationale is scientifically sound, the existing Phase 2 clinical data showing no PFS benefit in unselected populations raises concerns about translational efficacy. The key question is whether metformin achieves sufficient intratumoral concentrations to meaningfully activate AMPK in pancreatic tumor tissue.",

  "mechanistic_weaknesses": [
    {
      "concern": "Intratumoral drug concentration may be insufficient",
      "severity": "high",
      "explanation": "Metformin is a hydrophilic cation that relies on OCT1/OCT2 transporters for cellular uptake. PDAC's dense desmoplastic stroma may limit drug penetration. Plasma concentrations at standard doses (1-2g/day) are ~10-40 uM, but intratumoral levels are uncertain. In vitro studies showing anti-proliferative effects often use 1-10 mM -- 100x higher than achievable plasma levels."
    },
    {
      "concern": "AMPK activation may be context-dependent",
      "severity": "medium",
      "explanation": "AMPK activation in KRAS-mutant cells can paradoxically promote survival under metabolic stress by activating autophagy. The net effect of metformin in PDAC may depend on whether the anti-mTOR or pro-autophagy arm dominates, which varies by cell line and metabolic context."
    },
    {
      "concern": "OCT1 transporter polymorphism",
      "severity": "medium",
      "explanation": "SLC22A1 (OCT1) polymorphisms are common (20-30% of population) and substantially reduce metformin uptake. Without pharmacogenomic stratification, a trial population will include non-responders by genotype."
    }
  ],

  "evidence_gaps": [
    {
      "gap": "No intratumoral PK data in PDAC patients",
      "importance": "critical",
      "suggestion": "Conduct a window-of-opportunity study with pre-surgical metformin dosing and measure intratumoral AMPK phosphorylation by IHC"
    },
    {
      "gap": "No biomarker-stratified clinical data",
      "importance": "critical",
      "suggestion": "Retrospective analysis of NCT02005419 samples for OCT1 expression and KRAS variant-specific response"
    },
    {
      "gap": "Missing in vivo PDAC orthotopic model data with pharmacologically relevant dosing",
      "importance": "important",
      "suggestion": "KPC mouse model with metformin at doses achieving human-equivalent plasma levels"
    }
  ],

  "kill_criteria": [
    "Intratumoral metformin concentration < 100 uM in PDAC tissue (below AMPK activation threshold)",
    "No increase in pAMPK/pS6K ratio in pre/post-treatment biopsies",
    "OCT1-negative tumors show same response rate as OCT1-positive (would invalidate uptake mechanism)"
  ],

  "recommended_actions": [
    "Prioritize a window-of-opportunity PK/PD study over another large Phase 2",
    "Develop a companion diagnostic for OCT1 expression",
    "Test high-dose metformin (2.5g/day) in a Phase 1b to establish dose-exposure-response relationship",
    "Consider metformin prodrugs with improved tumor penetration (e.g., HL156A)"
  ]
}
```

---

### Claude Output: Confidence Assessment

```json
{
  "overall_confidence": 62,
  "confidence_level": "moderate",
  "summary": "The hypothesis is supported by strong mechanistic rationale and favorable safety profile, but tempered by mixed clinical data and unresolved intratumoral pharmacokinetic questions. The composite score of 72 is likely 5-10 points too high due to the Phase 2 negative result weighing less than it should in the current scoring model.",

  "probability_of_success": {
    "preclinical": 0.85,
    "phase1": 0.70,
    "phase2": 0.30,
    "overall": 0.18
  },

  "precedent_analysis": {
    "similar_successes": [
      "Thalidomide for multiple myeloma (metabolic modulator repurposed for cancer, now standard of care)",
      "Aspirin for colorectal cancer prevention (anti-inflammatory metabolic drug, strong epidemiological data)"
    ],
    "similar_failures": [
      "Metformin + standard chemo in NSCLC (Phase 2, negative, similar PK concerns)",
      "Hydroxychloroquine as autophagy inhibitor in PDAC (Phase 2, no benefit, likely inadequate target engagement)"
    ],
    "key_differences": "Metformin's advantage over hydroxychloroquine is a clearer target (AMPK vs. lysosomal pH) and stronger safety profile. Its disadvantage vs. thalidomide is the lack of a dramatic single-agent clinical response."
  },

  "strength_of_evidence": {
    "strongest_dimension": "safety (85) -- decades of human safety data is an unbeatable advantage",
    "weakest_dimension": "clinical_evidence (45) -- the Phase 2 negative result is the elephant in the room",
    "most_novel_aspect": "The DepMap CRISPR data showing PRKAB1 selective essentiality in pancreatic lineage provides genuinely new causal evidence beyond the correlative AMPK/mTOR story"
  },

  "recommendation": "proceed_with_caution"
}
```

---

## 5. Architecture Strengths

1. **Statistically rigorous scoring.** The `EvidenceScorer` uses Fisher's exact test with FDR correction, bootstrap confidence intervals, and pharmacological binding affinity modeling -- not arbitrary heuristics.

2. **11-dimension evidence framework.** Covers pathway overlap, expression, literature, clinical evidence, safety, novelty, DepMap causal dependency, GNN link prediction, mutation context, polypharmacology, and drug sensitivity screens. Each dimension produces transparent, traceable evidence records.

3. **6 discovery strategies.** Not limited to one approach: direct target, pathway-mediated, PPI network, expression-driven, literature-seeded, and analog discovery using pgvector cosine similarity on mechanism embeddings.

4. **Tiered LLM usage.** Claude Opus for high-scoring hypotheses (>=70), Sonnet for the rest. Rate limiting (5 concurrent, 50/min) and cost tracking built in.

5. **Stochastic ensemble algorithm (Tallula).** Detects hypotheses that deterministic scoring undervalues -- a genuinely novel approach to surfacing hidden signal.

6. **Full pipeline orchestration.** One-click pipeline with phase selection, live progress tracking, and phase-level error reporting.

---

## 6. Recommended UX Improvements (Priority Order)

| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 1 | Build typed renderers for LLM analysis JSON | Medium | High -- core value prop |
| 2 | Add confirmation modal for expensive pipeline triggers | Low | Medium -- prevent accidental spend |
| 3 | Add loading skeletons to dashboard | Low | Medium -- first impression |
| 4 | Show hypothesis titles in Tallula cards | Low | Medium -- context |
| 5 | Expand pipeline error messages | Low | Medium -- debugging |
| 6 | Pause polling on hidden tabs | Low | Low -- resource efficiency |
| 7 | Add page-level error boundaries | Low | Low -- resilience |
