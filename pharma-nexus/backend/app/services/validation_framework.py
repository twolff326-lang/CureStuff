"""Retrospective validation framework for drug repurposing hypothesis scoring.

This module provides the scientific rigor that transforms Pharma Nexus from
a tool into a validated method. It answers the fundamental question:

    "Does our scoring actually predict real-world repurposing outcomes?"

Components:
  1. Ground Truth Assembly — Known successful drug repurposing cases from
     RepoDB, FDA approval history, and literature-confirmed cases.
  2. Negative Controls — Known failed drug repurposing attempts from
     terminated Phase 2/3 clinical trials to avoid scoring false positives.
  3. Retrospective Validation — Rank recovery analysis: given known successes,
     where does our scoring rank them among all candidates?
  4. ROC/AUC Metrics — Area under the receiver operating characteristic curve
     measuring discrimination between true positives and negatives.
  5. Ablation Studies — Leave-one-dimension-out analysis to quantify each
     scoring dimension's contribution to predictive performance.
  6. Calibration Analysis — Does a score of 70 correspond to ~70% likelihood
     of being a true positive?
  7. Temporal Cross-Validation — Train on established cases, test on recent
     approvals to assess prospective predictive power.
  8. Benchmark Baselines — Compare scoring against random, single-dimension,
     and naive baselines to demonstrate added value.
  9. Sensitivity Analysis — Assess robustness of results to weight perturbations.

References:
  - Brown & Patel (2017). Drug repurposing: A review of current approaches.
  - Pushpakom et al. (2019). Drug repurposing: progress, challenges and
    recommendations. Nature Reviews Drug Discovery.
"""

import logging
from typing import Any

import numpy as np
from scipy import stats as scipy_stats
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    auc,
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, TrialDrug
from app.models.hypothesis import Hypothesis
from app.models.target import Target
from app.services.scoring_config import DIMENSIONS, ScoringConfig

logger = logging.getLogger(__name__)


# ===================================================================
# GROUND TRUTH: Known drug repurposing successes
# ===================================================================

# Curated from FDA approvals, RepoDB, and landmark repurposing papers.
# Format: (drug_name_fragment, cancer_name_fragment, evidence_level)
# evidence_level: "fda_approved" | "phase3_success" | "phase2_positive" | "preclinical_validated"
#
# 132 cases across 4 evidence tiers. Organized by:
#   1. Non-cancer → cancer repurposing (FDA-approved)
#   2. Cancer indication expansions (FDA-approved)
#   3. Phase 3 successes
#   4. Phase 2 positive results (non-cancer drugs with clinical anticancer signal)
#   5. Preclinical validated (strong in vitro/in vivo evidence, peer-reviewed)
KNOWN_REPURPOSING_CASES = [
    # ===================================================================
    # FDA-APPROVED: NON-CANCER → CANCER REPURPOSING
    # Drugs originally developed/approved for non-cancer indications,
    # subsequently granted FDA approval for cancer treatment.
    # ===================================================================

    # Thalidomide & IMiDs (sedative/anti-leprosy → hematologic cancers)
    ("thalidomide", "multiple myeloma", "fda_approved"),
    ("lenalidomide", "multiple myeloma", "fda_approved"),
    ("pomalidomide", "multiple myeloma", "fda_approved"),

    # Retinoids (dermatology → hematologic cancers)
    ("all-trans retinoic acid", "acute promyelocytic leukemia", "fda_approved"),
    ("bexarotene", "cutaneous t-cell lymphoma", "fda_approved"),

    # Traditional remedy → APL
    ("arsenic trioxide", "acute promyelocytic leukemia", "fda_approved"),

    # Corticosteroids (anti-inflammatory → hematologic cancers)
    ("dexamethasone", "multiple myeloma", "fda_approved"),

    # HDAC inhibitors (neuropsych/antibiotic screening → cancer)
    ("vorinostat", "cutaneous t-cell lymphoma", "fda_approved"),
    ("romidepsin", "cutaneous t-cell lymphoma", "fda_approved"),
    ("belinostat", "peripheral t-cell lymphoma", "fda_approved"),
    ("panobinostat", "multiple myeloma", "fda_approved"),

    # mTOR inhibitors (immunosuppressant → cancer)
    ("rapamycin", "renal cell", "fda_approved"),
    ("everolimus", "breast", "fda_approved"),
    ("everolimus", "renal cell", "fda_approved"),
    ("everolimus", "pancreatic", "fda_approved"),
    ("temsirolimus", "renal cell", "fda_approved"),

    # Proteasome inhibitors (expanded indication)
    ("bortezomib", "mantle cell lymphoma", "fda_approved"),

    # Hormonal agents (contraception/osteoporosis → cancer)
    ("tamoxifen", "breast", "fda_approved"),
    ("raloxifene", "breast", "fda_approved"),

    # Antimetabolites (antiviral nucleoside → cancer)
    ("methotrexate", "breast", "fda_approved"),
    ("gemcitabine", "pancreatic", "fda_approved"),
    ("gemcitabine", "bladder", "fda_approved"),
    ("gemcitabine", "lung", "fda_approved"),

    # Biologics (antiviral → cancer)
    ("interferon alfa", "melanoma", "fda_approved"),
    ("interferon alfa", "renal cell", "fda_approved"),
    ("interferon alfa", "leukemia", "fda_approved"),

    # Bone agents (osteoporosis → cancer)
    ("zoledronic acid", "multiple myeloma", "fda_approved"),
    ("denosumab", "giant cell tumor", "fda_approved"),

    # Hydroxyurea (sickle cell → leukemia)
    ("hydroxyurea", "leukemia", "fda_approved"),

    # ===================================================================
    # FDA-APPROVED: CANCER INDICATION EXPANSIONS
    # Drugs approved for one cancer type, subsequently approved for
    # a mechanistically distinct cancer type.
    # ===================================================================

    # Imatinib (CML → GIST via KIT)
    ("imatinib", "gastrointestinal stromal", "fda_approved"),

    # Anti-angiogenics across tumor types
    ("bevacizumab", "colorectal", "fda_approved"),
    ("bevacizumab", "lung", "fda_approved"),
    ("bevacizumab", "glioblastoma", "fda_approved"),
    ("bevacizumab", "ovarian", "fda_approved"),
    ("bevacizumab", "cervical", "fda_approved"),
    ("sorafenib", "hepatocellular", "fda_approved"),
    ("sorafenib", "renal cell", "fda_approved"),
    ("sorafenib", "thyroid", "fda_approved"),

    # Multi-kinase inhibitors across tumor types
    ("sunitinib", "gastrointestinal stromal", "fda_approved"),
    ("sunitinib", "pancreatic", "fda_approved"),
    ("pazopanib", "soft tissue sarcoma", "fda_approved"),
    ("lenvatinib", "hepatocellular", "fda_approved"),
    ("lenvatinib", "endometrial", "fda_approved"),
    ("cabozantinib", "hepatocellular", "fda_approved"),
    ("cabozantinib", "renal cell", "fda_approved"),

    # Checkpoint inhibitors across tumor types
    ("nivolumab", "melanoma", "fda_approved"),
    ("nivolumab", "lung", "fda_approved"),
    ("nivolumab", "renal cell", "fda_approved"),
    ("nivolumab", "bladder", "fda_approved"),
    ("nivolumab", "head and neck", "fda_approved"),
    ("nivolumab", "hepatocellular", "fda_approved"),
    ("pembrolizumab", "lung", "fda_approved"),
    ("pembrolizumab", "melanoma", "fda_approved"),
    ("pembrolizumab", "head and neck", "fda_approved"),
    ("pembrolizumab", "bladder", "fda_approved"),
    ("pembrolizumab", "gastric", "fda_approved"),
    ("pembrolizumab", "cervical", "fda_approved"),
    ("ipilimumab", "melanoma", "fda_approved"),
    ("ipilimumab", "renal cell", "fda_approved"),

    # PARP inhibitors across tumor types (ovarian → others)
    ("olaparib", "breast", "fda_approved"),
    ("olaparib", "prostate", "fda_approved"),
    ("olaparib", "pancreatic", "fda_approved"),

    # Taxanes across tumor types
    ("docetaxel", "prostate", "fda_approved"),
    ("paclitaxel", "breast", "fda_approved"),
    ("paclitaxel", "lung", "fda_approved"),

    # HER2 antibodies (breast → gastric)
    ("trastuzumab", "gastric", "fda_approved"),

    # Alkylating agent (melanoma → GBM)
    ("temozolomide", "glioblastoma", "fda_approved"),

    # Anti-CD20 (lymphoma → leukemia)
    ("rituximab", "lymphoma", "fda_approved"),
    ("rituximab", "leukemia", "fda_approved"),

    # ===================================================================
    # PHASE 3 SUCCESSES
    # Large randomized trials demonstrating significant benefit.
    # ===================================================================
    ("aspirin", "colorectal", "phase3_success"),
    ("zoledronic acid", "breast", "phase3_success"),
    ("celecoxib", "colorectal", "phase3_success"),
    ("clodronate", "breast", "phase3_success"),
    ("pamidronate", "multiple myeloma", "phase3_success"),

    # ===================================================================
    # PHASE 2 POSITIVE
    # Non-cancer drugs with positive Phase 2 clinical trial results
    # demonstrating anticancer activity.
    # ===================================================================

    # Metformin (diabetes → multiple cancer types)
    ("metformin", "breast", "phase2_positive"),
    ("metformin", "colorectal", "phase2_positive"),
    ("metformin", "endometrial", "phase2_positive"),
    ("metformin", "prostate", "phase2_positive"),
    ("metformin", "pancreatic", "phase2_positive"),
    ("metformin", "ovarian", "phase2_positive"),
    ("metformin", "lung", "phase2_positive"),

    # Beta-blockers (cardiovascular → cancer)
    ("propranolol", "melanoma", "phase2_positive"),
    ("propranolol", "angiosarcoma", "phase2_positive"),

    # Antifungals (Hedgehog/angiogenesis inhibition)
    ("itraconazole", "lung", "phase2_positive"),
    ("itraconazole", "prostate", "phase2_positive"),
    ("itraconazole", "basal cell", "phase2_positive"),

    # Autophagy inhibitors (antimalarials → cancer)
    ("chloroquine", "glioblastoma", "phase2_positive"),
    ("hydroxychloroquine", "pancreatic", "phase2_positive"),

    # Anticonvulsants (HDAC inhibition)
    ("valproic acid", "leukemia", "phase2_positive"),
    ("valproic acid", "cervical", "phase2_positive"),

    # Alcohol cessation (ALDH/proteasome inhibition)
    ("disulfiram", "glioblastoma", "phase2_positive"),

    # HIV antivirals (PI/AKT pathway inhibition)
    ("nelfinavir", "cervical", "phase2_positive"),
    ("ritonavir", "kaposi sarcoma", "phase2_positive"),

    # H2 receptor antagonist (immune modulation)
    ("cimetidine", "colorectal", "phase2_positive"),

    # Antimalarial (ROS/iron-mediated apoptosis)
    ("artesunate", "colorectal", "phase2_positive"),

    # Antipsychotics (dopamine receptor / cancer stem cells)
    ("thioridazine", "leukemia", "phase2_positive"),

    # Antibiotics (anti-angiogenic/immunomodulatory)
    ("doxycycline", "lymphoma", "phase2_positive"),
    ("clarithromycin", "multiple myeloma", "phase2_positive"),

    # Antiparasitic (proteasome/NF-kB)
    ("suramin", "prostate", "phase2_positive"),

    # Antitussive (tubulin binding)
    ("noscapine", "lung", "phase2_positive"),

    # Rheumatoid arthritis (DHODH inhibition)
    ("leflunomide", "prostate", "phase2_positive"),

    # IBD drug (xCT transporter inhibition)
    ("sulfasalazine", "glioblastoma", "phase2_positive"),

    # Statins (mevalonate pathway inhibition)
    ("simvastatin", "colorectal", "phase2_positive"),
    ("lovastatin", "leukemia", "phase2_positive"),
    ("atorvastatin", "breast", "phase2_positive"),

    # NSAIDs (COX-2 / Wnt pathway)
    ("celecoxib", "lung", "phase2_positive"),
    ("indomethacin", "colorectal", "phase2_positive"),

    # Antiparasitics (tubulin / Wnt / STAT3)
    ("mebendazole", "colorectal", "phase2_positive"),
    ("niclosamide", "prostate", "phase2_positive"),

    # ===================================================================
    # PRECLINICAL VALIDATED
    # Strong in vitro/in vivo evidence published in peer-reviewed
    # journals. Multiple independent studies confirming activity.
    # ===================================================================

    # Statins (generic class-level evidence)
    ("statins", "colorectal", "preclinical_validated"),

    # Benzimidazole anthelmintics
    ("mebendazole", "glioblastoma", "preclinical_validated"),
    ("albendazole", "hepatocellular", "preclinical_validated"),
    ("flubendazole", "leukemia", "preclinical_validated"),
    ("flubendazole", "melanoma", "preclinical_validated"),

    # Halogenated salicylanilides
    ("niclosamide", "colorectal", "preclinical_validated"),

    # Gold compounds (thioredoxin reductase inhibition)
    ("auranofin", "leukemia", "preclinical_validated"),
    ("auranofin", "ovarian", "preclinical_validated"),

    # Antiparasitic ionophore (Wnt pathway)
    ("pyrvinium", "colorectal", "preclinical_validated"),

    # Polyether antibiotic (cancer stem cells — Gupta et al. 2009 Cell)
    ("salinomycin", "breast", "preclinical_validated"),

    # Photosensitizer (YAP-TEAD pathway)
    ("verteporfin", "hepatocellular", "preclinical_validated"),

    # Metabolic modulator (PDK inhibition, Warburg effect)
    ("dichloroacetate", "glioblastoma", "preclinical_validated"),

    # Avermectin antiparasitic (WNT-TCF, PAK1, multiple pathways)
    ("ivermectin", "breast", "preclinical_validated"),
    ("ivermectin", "leukemia", "preclinical_validated"),

    # Thiazolide antiparasitic (Wnt/glutaminolysis)
    ("nitazoxanide", "colorectal", "preclinical_validated"),

    # PPAR-alpha agonist (metabolic disruption)
    ("fenofibrate", "glioblastoma", "preclinical_validated"),

    # Antipsychotics (STAT5/dopamine receptor)
    ("pimozide", "breast", "preclinical_validated"),
    ("chlorpromazine", "glioblastoma", "preclinical_validated"),

    # NSAID (COX-independent mechanisms)
    ("piroxicam", "bladder", "preclinical_validated"),

    # Cardiac glycoside (Na/K-ATPase, Src signaling)
    ("digoxin", "prostate", "preclinical_validated"),

    # Alcohol cessation drug (additional cancer types beyond GBM)
    ("disulfiram", "breast", "preclinical_validated"),
    ("disulfiram", "lung", "preclinical_validated"),

    # mTOR inhibitor (additional cancer type)
    ("rapamycin", "mantle cell lymphoma", "preclinical_validated"),
]


# ===================================================================
# NEGATIVE CONTROLS: Known drug repurposing failures
# ===================================================================

# Curated from terminated Phase 2/3 clinical trials where drugs
# failed to show efficacy for the tested cancer indication.
# These serve as true negatives for validation: the system SHOULD
# score these lower than known successes.
#
# Format: (drug_name_fragment, cancer_name_fragment, failure_stage, reason)
# failure_stage: "phase3_failed" | "phase2_failed" | "withdrawn"
# reason: brief explanation of why the drug failed
KNOWN_REPURPOSING_FAILURES = [
    # ===================================================================
    # PHASE 3 FAILURES: Drugs that failed Phase 3 for cancer indication
    # ===================================================================
    #
    # NOTE: Excluded pairs that also appear in KNOWN_REPURPOSING_CASES to
    # avoid DB-level contradictions (fragment matching can't distinguish
    # clinical settings like adjuvant vs metastatic):
    #   - bevacizumab + colorectal (FDA-approved metastatic, failed adjuvant)
    #   - metformin + pancreatic (Phase 2 positive early-stage, Phase 3 MAPPA failed advanced)
    #   - sorafenib + hepatocellular (FDA-approved advanced, failed adjuvant STORM)

    # Celecoxib failed in APC prevention (terminated due to cardiovascular risk)
    ("celecoxib", "pancreatic", "phase3_failed", "no survival benefit, cardiovascular risk"),

    # Cimetidine failed Phase 3 for gastric cancer (Japanese RCT)
    ("cimetidine", "gastric", "phase3_failed", "no survival benefit"),

    # Chloroquine/HCQ failed in multiple Phase 2/3 combinations
    ("hydroxychloroquine", "lung", "phase3_failed", "no improvement in autophagy-targeted therapy"),

    # Fenretinide (retinoid) failed breast cancer prevention Phase 3
    ("fenretinide", "breast", "phase3_failed", "no benefit in secondary prevention"),

    # Iniparib (misidentified PARP inhibitor) failed Phase 3 TNBC
    ("iniparib", "breast", "phase3_failed", "not actually a PARP inhibitor, no PFS benefit"),

    # Enzastaurin (PKC-beta inhibitor) failed Phase 3 GBM
    ("enzastaurin", "glioblastoma", "phase3_failed", "no PFS benefit vs lomustine"),

    # Bavituximab (anti-phosphatidylserine) failed Phase 3 NSCLC
    ("bavituximab", "lung", "phase3_failed", "no OS benefit with docetaxel"),

    # ===================================================================
    # PHASE 2 FAILURES: Drugs that showed no efficacy in Phase 2
    # ===================================================================

    # Disulfiram failed Phase 2 for liver cancer
    ("disulfiram", "hepatocellular", "phase2_failed", "no objective response"),

    # Valproic acid no benefit in solid tumors (multiple Phase 2)
    ("valproic acid", "lung", "phase2_failed", "no single-agent activity"),
    ("valproic acid", "melanoma", "phase2_failed", "no objective response"),

    # Cimetidine no benefit in NSCLC
    ("cimetidine", "lung", "phase2_failed", "no clinical activity"),

    # Nelfinavir no benefit in adenoid cystic carcinoma
    ("nelfinavir", "head and neck", "phase2_failed", "no RECIST response"),

    # Chloroquine no benefit in NSCLC
    ("chloroquine", "lung", "phase2_failed", "no improvement with erlotinib"),

    # Statins failed Phase 2 in hepatocellular
    ("simvastatin", "hepatocellular", "phase2_failed", "no single-agent activity"),
    ("pravastatin", "hepatocellular", "phase2_failed", "no survival benefit"),

    # Thioridazine failed for solid tumors (toxicity limited)
    ("thioridazine", "breast", "phase2_failed", "QTc prolongation, no efficacy"),

    # Itraconazole no benefit in ovarian cancer
    ("itraconazole", "ovarian", "phase2_failed", "no single-agent activity"),

    # Propranolol no benefit in melanoma as single agent (Phase 2)
    ("propranolol", "lung", "phase2_failed", "no single-agent activity"),

    # Suramin neurotoxicity limited Phase 2 for non-prostate cancers
    ("suramin", "lung", "phase2_failed", "severe neurotoxicity, no efficacy"),

    # Doxycycline no benefit in prostate cancer
    ("doxycycline", "prostate", "phase2_failed", "no PSA response"),

    # Auranofin no benefit in GBM (Phase 2 terminated)
    ("auranofin", "glioblastoma", "phase2_failed", "poor CNS penetration, no response"),

    # Sulfasalazine failed Phase 2 for non-GBM brain tumors
    ("sulfasalazine", "breast", "phase2_failed", "no systemic activity"),

    # ===================================================================
    # WITHDRAWN: Drugs withdrawn from cancer indication after initial approval
    # ===================================================================

    # Bevacizumab breast cancer approval withdrawn by FDA (2011)
    ("bevacizumab", "breast", "withdrawn", "FDA withdrew approval, no OS benefit"),

    # Gemtuzumab ozogamicin initially withdrawn (later re-approved at lower dose)
    ("gemtuzumab", "leukemia", "withdrawn", "veno-occlusive disease, initially pulled"),

    # Ponatinib briefly withdrawn for vascular events
    ("ponatinib", "leukemia", "withdrawn", "serious vascular events"),
]


# ===================================================================
# TEMPORAL COHORTS: Ground truth split by approval era
# ===================================================================

# Cases grouped by era for temporal cross-validation.
# "established" cases (pre-2010) serve as training data.
# "recent" cases (2010+) serve as held-out test data.
TEMPORAL_COHORTS = {
    "established": [
        # Pre-2010 FDA approvals — these are the training set
        ("thalidomide", "multiple myeloma", "fda_approved"),
        ("lenalidomide", "multiple myeloma", "fda_approved"),
        ("all-trans retinoic acid", "acute promyelocytic leukemia", "fda_approved"),
        ("arsenic trioxide", "acute promyelocytic leukemia", "fda_approved"),
        ("vorinostat", "cutaneous t-cell lymphoma", "fda_approved"),
        ("bortezomib", "mantle cell lymphoma", "fda_approved"),
        ("tamoxifen", "breast", "fda_approved"),
        ("methotrexate", "breast", "fda_approved"),
        ("gemcitabine", "pancreatic", "fda_approved"),
        ("gemcitabine", "bladder", "fda_approved"),
        ("gemcitabine", "lung", "fda_approved"),
        ("interferon alfa", "melanoma", "fda_approved"),
        ("interferon alfa", "renal cell", "fda_approved"),
        ("interferon alfa", "leukemia", "fda_approved"),
        ("imatinib", "gastrointestinal stromal", "fda_approved"),
        ("bevacizumab", "colorectal", "fda_approved"),
        ("sorafenib", "renal cell", "fda_approved"),
        ("sunitinib", "gastrointestinal stromal", "fda_approved"),
        ("temozolomide", "glioblastoma", "fda_approved"),
        ("rituximab", "lymphoma", "fda_approved"),
        ("rituximab", "leukemia", "fda_approved"),
        ("dexamethasone", "multiple myeloma", "fda_approved"),
        ("rapamycin", "renal cell", "fda_approved"),
        ("hydroxyurea", "leukemia", "fda_approved"),
        ("bexarotene", "cutaneous t-cell lymphoma", "fda_approved"),
        ("romidepsin", "cutaneous t-cell lymphoma", "fda_approved"),
        ("temsirolimus", "renal cell", "fda_approved"),
        ("paclitaxel", "breast", "fda_approved"),
        ("paclitaxel", "lung", "fda_approved"),
        ("docetaxel", "prostate", "fda_approved"),
        ("raloxifene", "breast", "fda_approved"),
    ],
    "recent": [
        # 2010+ FDA approvals — these are the held-out test set
        ("pomalidomide", "multiple myeloma", "fda_approved"),
        ("belinostat", "peripheral t-cell lymphoma", "fda_approved"),
        ("panobinostat", "multiple myeloma", "fda_approved"),
        ("everolimus", "breast", "fda_approved"),
        ("everolimus", "renal cell", "fda_approved"),
        ("everolimus", "pancreatic", "fda_approved"),
        ("bevacizumab", "lung", "fda_approved"),
        ("bevacizumab", "glioblastoma", "fda_approved"),
        ("bevacizumab", "ovarian", "fda_approved"),
        ("bevacizumab", "cervical", "fda_approved"),
        ("sorafenib", "hepatocellular", "fda_approved"),
        ("sorafenib", "thyroid", "fda_approved"),
        ("sunitinib", "pancreatic", "fda_approved"),
        ("pazopanib", "soft tissue sarcoma", "fda_approved"),
        ("lenvatinib", "hepatocellular", "fda_approved"),
        ("lenvatinib", "endometrial", "fda_approved"),
        ("cabozantinib", "hepatocellular", "fda_approved"),
        ("cabozantinib", "renal cell", "fda_approved"),
        ("nivolumab", "melanoma", "fda_approved"),
        ("nivolumab", "lung", "fda_approved"),
        ("nivolumab", "renal cell", "fda_approved"),
        ("nivolumab", "bladder", "fda_approved"),
        ("nivolumab", "head and neck", "fda_approved"),
        ("nivolumab", "hepatocellular", "fda_approved"),
        ("pembrolizumab", "lung", "fda_approved"),
        ("pembrolizumab", "melanoma", "fda_approved"),
        ("pembrolizumab", "head and neck", "fda_approved"),
        ("pembrolizumab", "bladder", "fda_approved"),
        ("pembrolizumab", "gastric", "fda_approved"),
        ("pembrolizumab", "cervical", "fda_approved"),
        ("ipilimumab", "melanoma", "fda_approved"),
        ("ipilimumab", "renal cell", "fda_approved"),
        ("olaparib", "breast", "fda_approved"),
        ("olaparib", "prostate", "fda_approved"),
        ("olaparib", "pancreatic", "fda_approved"),
        ("trastuzumab", "gastric", "fda_approved"),
        ("denosumab", "giant cell tumor", "fda_approved"),
        ("zoledronic acid", "multiple myeloma", "fda_approved"),
    ],
}


class ValidationFramework:
    """Retrospective validation and performance assessment for the scoring system.

    Answers: "If we had used Pharma Nexus in the past, would it have
    identified the drug repurposing successes we now know about?"
    """

    def __init__(self):
        self.config = ScoringConfig()

    # ==================================================================
    # Ground Truth Assembly
    # ==================================================================

    async def build_ground_truth(
        self, db: AsyncSession
    ) -> dict[str, Any]:
        """Match known repurposing cases to drug-cancer pairs in our database.

        Returns:
            dict with matched_cases, unmatched_cases, coverage statistics
        """
        matched = []
        unmatched = []

        for drug_fragment, cancer_fragment, evidence_level in KNOWN_REPURPOSING_CASES:
            # Find matching drug
            drug_result = await db.execute(
                select(Drug.id, Drug.name).where(
                    Drug.name.ilike(f"%{drug_fragment}%")
                ).limit(1)
            )
            drug_row = drug_result.first()

            # Find matching cancer type
            cancer_result = await db.execute(
                select(CancerType.id, CancerType.name).where(
                    CancerType.name.ilike(f"%{cancer_fragment}%")
                ).limit(1)
            )
            cancer_row = cancer_result.first()

            if drug_row and cancer_row:
                matched.append({
                    "drug_id": drug_row[0],
                    "drug_name": drug_row[1],
                    "cancer_type_id": cancer_row[0],
                    "cancer_name": cancer_row[1],
                    "evidence_level": evidence_level,
                    "ground_truth_label": 1,
                })
            else:
                unmatched.append({
                    "drug_fragment": drug_fragment,
                    "cancer_fragment": cancer_fragment,
                    "evidence_level": evidence_level,
                    "drug_found": drug_row is not None,
                    "cancer_found": cancer_row is not None,
                })

        # Evidence level weights for weighted metrics
        level_weights = {
            "fda_approved": 1.0,
            "phase3_success": 0.8,
            "phase2_positive": 0.5,
            "preclinical_validated": 0.3,
        }

        for case in matched:
            case["weight"] = level_weights.get(case["evidence_level"], 0.3)

        return {
            "matched_cases": matched,
            "unmatched_cases": unmatched,
            "n_matched": len(matched),
            "n_unmatched": len(unmatched),
            "coverage": len(matched) / len(KNOWN_REPURPOSING_CASES) if KNOWN_REPURPOSING_CASES else 0,
            "by_evidence_level": {
                level: sum(1 for c in matched if c["evidence_level"] == level)
                for level in level_weights
            },
        }

    # ==================================================================
    # Retrospective Validation
    # ==================================================================

    async def run_retrospective_validation(
        self,
        db: AsyncSession,
        ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Full retrospective validation pipeline.

        1. Build ground truth from known repurposing cases
        2. Get all scored hypotheses from the database
        3. Label each hypothesis as positive (known success) or negative
        4. Compute rank recovery, ROC-AUC, PR-AUC
        5. Return detailed performance metrics

        Args:
            ground_truth: Pre-built ground truth dict (avoids redundant DB queries)

        Returns:
            dict with comprehensive validation results
        """
        # Step 1: Ground truth
        if ground_truth is None:
            ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {
                "error": "No ground truth cases matched database entries",
                "ground_truth": ground_truth,
            }

        # Step 2: Get all hypotheses
        result = await db.execute(
            select(Hypothesis).order_by(Hypothesis.composite_score.desc())
        )
        all_hypotheses = result.scalars().all()

        if not all_hypotheses:
            return {
                "error": "No hypotheses scored yet",
                "ground_truth": ground_truth,
            }

        # Step 3: Label
        labels = []
        scores = []
        ranks = []
        positive_ranks = []
        case_details = []

        for rank, hyp in enumerate(all_hypotheses, 1):
            pair = (hyp.drug_id, hyp.cancer_type_id)
            is_positive = pair in positive_pairs
            labels.append(1 if is_positive else 0)
            scores.append(hyp.composite_score)
            ranks.append(rank)

            if is_positive:
                positive_ranks.append(rank)
                # Find the ground truth case for weight
                case = next(
                    (c for c in ground_truth["matched_cases"]
                     if c["drug_id"] == hyp.drug_id
                     and c["cancer_type_id"] == hyp.cancer_type_id),
                    None,
                )
                case_details.append({
                    "drug_id": hyp.drug_id,
                    "cancer_type_id": hyp.cancer_type_id,
                    "title": hyp.title,
                    "composite_score": hyp.composite_score,
                    "rank": rank,
                    "percentile": round((1 - rank / len(all_hypotheses)) * 100, 1),
                    "evidence_level": case["evidence_level"] if case else "unknown",
                    "weight": case["weight"] if case else 0.3,
                    "dimension_scores": {
                        "pathway_overlap": hyp.pathway_overlap_score,
                        "expression_correlation": hyp.expression_correlation_score,
                        "literature_support": hyp.literature_support_score,
                        "clinical_evidence": hyp.clinical_evidence_score,
                        "safety": hyp.safety_score,
                        "novelty": hyp.novelty_score,
                    },
                })

        # Step 4: Compute metrics
        labels_arr = np.array(labels)
        scores_arr = np.array(scores)
        n_total = len(all_hypotheses)
        n_positive = int(labels_arr.sum())

        metrics = {}

        # Rank recovery metrics
        if positive_ranks:
            metrics["rank_recovery"] = {
                "mean_rank": round(float(np.mean(positive_ranks)), 1),
                "median_rank": round(float(np.median(positive_ranks)), 1),
                "mean_percentile": round(
                    float(np.mean([1 - r / n_total for r in positive_ranks])) * 100, 1
                ),
                "top_1pct": sum(1 for r in positive_ranks if r <= n_total * 0.01),
                "top_5pct": sum(1 for r in positive_ranks if r <= n_total * 0.05),
                "top_10pct": sum(1 for r in positive_ranks if r <= n_total * 0.10),
                "top_25pct": sum(1 for r in positive_ranks if r <= n_total * 0.25),
                "n_positive": n_positive,
                "n_total": n_total,
            }

            # Mann-Whitney U test: are positive scores significantly higher?
            pos_scores = scores_arr[labels_arr == 1]
            neg_scores = scores_arr[labels_arr == 0]
            if len(pos_scores) > 0 and len(neg_scores) > 0:
                u_stat, u_pval = scipy_stats.mannwhitneyu(
                    pos_scores, neg_scores, alternative="greater"
                )
                metrics["rank_recovery"]["mann_whitney_u"] = float(u_stat)
                metrics["rank_recovery"]["mann_whitney_p"] = float(u_pval)
                metrics["rank_recovery"]["pos_mean_score"] = round(float(pos_scores.mean()), 2)
                metrics["rank_recovery"]["neg_mean_score"] = round(float(neg_scores.mean()), 2)

        # ROC-AUC
        if n_positive > 0 and n_positive < n_total:
            try:
                fpr, tpr, thresholds = roc_curve(labels_arr, scores_arr)
                roc_auc = float(auc(fpr, tpr))
                metrics["roc"] = {
                    "auc": round(roc_auc, 4),
                    "interpretation": _interpret_auc(roc_auc),
                    "fpr": fpr.tolist()[:50],  # subsample for API response size
                    "tpr": tpr.tolist()[:50],
                }
            except Exception as e:
                logger.warning("ROC-AUC computation failed: %s", e)

            # PR-AUC (more informative for imbalanced data)
            try:
                precision, recall, _ = precision_recall_curve(labels_arr, scores_arr)
                pr_auc = float(average_precision_score(labels_arr, scores_arr))
                metrics["precision_recall"] = {
                    "auc": round(pr_auc, 4),
                    "baseline": round(n_positive / n_total, 4),
                    "lift": round(pr_auc / (n_positive / n_total), 2) if n_positive > 0 else 0,
                }
            except Exception as e:
                logger.warning("PR-AUC computation failed: %s", e)

        # Enrichment at various thresholds
        thresholds = [25, 50, 75]
        enrichment = {}
        for thresh in thresholds:
            above = labels_arr[scores_arr >= thresh]
            if len(above) > 0:
                precision_at_thresh = float(above.sum()) / len(above)
                baseline = n_positive / n_total
                enrichment[f"score_ge_{thresh}"] = {
                    "n_hypotheses": int(len(above)),
                    "n_true_positives": int(above.sum()),
                    "precision": round(precision_at_thresh, 4),
                    "enrichment_fold": round(
                        precision_at_thresh / baseline, 2
                    ) if baseline > 0 else 0.0,
                }
        metrics["enrichment_at_thresholds"] = enrichment

        return {
            "ground_truth_summary": {
                "n_matched": ground_truth["n_matched"],
                "n_unmatched": ground_truth["n_unmatched"],
                "coverage": ground_truth["coverage"],
                "by_evidence_level": ground_truth["by_evidence_level"],
            },
            "dataset": {
                "n_hypotheses": n_total,
                "n_positive": n_positive,
                "n_negative": n_total - n_positive,
                "positive_rate": round(n_positive / n_total, 6) if n_total > 0 else 0,
            },
            "metrics": metrics,
            "case_details": sorted(case_details, key=lambda x: x["rank"]),
        }

    # ==================================================================
    # Ablation Studies
    # ==================================================================

    async def run_ablation_study(
        self,
        db: AsyncSession,
        ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Leave-one-dimension-out ablation study.

        For each of the 11 scoring dimensions, re-compute composite scores
        with that dimension zeroed out and measure the impact on ROC-AUC.

        Answers: "Which dimensions actually matter for prediction?"

        Returns:
            dict with per-dimension ablation results and importance ranking
        """
        if ground_truth is None:
            ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {"error": "No ground truth cases matched"}

        # Get all hypotheses with dimension scores
        result = await db.execute(select(Hypothesis))
        all_hypotheses = result.scalars().all()

        if len(all_hypotheses) < 10:
            return {"error": "Too few hypotheses for meaningful ablation"}

        # Get active weights
        weights = await self.config.get_active_weights(db)

        # Labels
        labels = np.array([
            1 if (h.drug_id, h.cancer_type_id) in positive_pairs else 0
            for h in all_hypotheses
        ])

        n_positive = int(labels.sum())
        if n_positive == 0 or n_positive == len(labels):
            return {"error": "Need both positive and negative cases for ablation"}

        # Baseline: full model AUC
        baseline_scores = np.array([h.composite_score for h in all_hypotheses])
        baseline_auc = float(roc_auc_score(labels, baseline_scores))

        # Ablation for each dimension
        ablation_results = {}
        for dim in DIMENSIONS:
            # Recompute composite with this dimension zeroed out
            ablated_weights = weights.copy()
            removed_weight = ablated_weights.pop(dim)

            # Redistribute removed weight proportionally
            remaining_total = sum(ablated_weights.values())
            if remaining_total > 0:
                for k in ablated_weights:
                    ablated_weights[k] *= 1.0 / remaining_total
            else:
                continue

            ablated_scores = []
            for h in all_hypotheses:
                dim_scores = {
                    "pathway_overlap": {"score": h.pathway_overlap_score or 0},
                    "expression_correlation": {"score": h.expression_correlation_score or 0},
                    "literature_support": {"score": h.literature_support_score or 0},
                    "clinical_evidence": {"score": h.clinical_evidence_score or 0},
                    "safety": {"score": h.safety_score or 0},
                    "novelty": {"score": h.novelty_score or 0},
                }
                # Zero out the ablated dimension
                dim_scores[dim] = {"score": 0}
                score = self.config.compute_composite_score(dim_scores, ablated_weights)
                ablated_scores.append(score)

            ablated_arr = np.array(ablated_scores)
            ablated_auc = float(roc_auc_score(labels, ablated_arr))

            auc_drop = baseline_auc - ablated_auc
            ablation_results[dim] = {
                "ablated_auc": round(ablated_auc, 4),
                "auc_drop": round(auc_drop, 4),
                "relative_importance": round(
                    auc_drop / baseline_auc * 100, 2
                ) if baseline_auc > 0 else 0.0,
                "original_weight": weights[dim],
            }

        # Rank dimensions by importance (AUC drop)
        importance_ranking = sorted(
            ablation_results.items(),
            key=lambda x: x[1]["auc_drop"],
            reverse=True,
        )

        return {
            "baseline_auc": round(baseline_auc, 4),
            "baseline_interpretation": _interpret_auc(baseline_auc),
            "n_hypotheses": len(all_hypotheses),
            "n_positive": n_positive,
            "ablation_results": ablation_results,
            "importance_ranking": [
                {"dimension": dim, **data}
                for dim, data in importance_ranking
            ],
            "recommendation": _generate_weight_recommendation(
                ablation_results, weights
            ),
        }

    # ==================================================================
    # Calibration Analysis
    # ==================================================================

    async def run_calibration_analysis(
        self,
        db: AsyncSession,
        n_bins: int = 10,
        ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assess score calibration: does a score of X mean X% probability?

        Returns:
            dict with calibration curve data, Brier score, expected calibration error
        """
        if ground_truth is None:
            ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {"error": "No ground truth cases matched"}

        result = await db.execute(select(Hypothesis))
        all_hypotheses = result.scalars().all()

        labels = np.array([
            1 if (h.drug_id, h.cancer_type_id) in positive_pairs else 0
            for h in all_hypotheses
        ])
        scores = np.array([h.composite_score / 100.0 for h in all_hypotheses])

        n_positive = int(labels.sum())
        if n_positive < 2:
            return {"error": "Need at least 2 positive cases for calibration"}

        # Calibration curve
        try:
            prob_true, prob_pred = calibration_curve(
                labels, scores, n_bins=n_bins, strategy="uniform"
            )
        except Exception:
            prob_true = np.array([])
            prob_pred = np.array([])

        # Brier score (mean squared error of probability estimates)
        brier = float(np.mean((scores - labels) ** 2))

        # Expected Calibration Error
        ece = 0.0
        if len(prob_true) > 0:
            bin_sizes = np.histogram(scores, bins=n_bins, range=(0, 1))[0]
            total = bin_sizes.sum()
            for i in range(len(prob_true)):
                if i < len(bin_sizes) and total > 0:
                    weight = bin_sizes[i] / total
                    ece += weight * abs(prob_true[i] - prob_pred[i])

        return {
            "n_hypotheses": len(all_hypotheses),
            "n_positive": n_positive,
            "brier_score": round(brier, 6),
            "expected_calibration_error": round(float(ece), 4),
            "interpretation": _interpret_calibration(brier),
            "calibration_curve": {
                "predicted_probability": prob_pred.tolist(),
                "observed_frequency": prob_true.tolist(),
            },
        }

    # ==================================================================
    # Null Distribution Generation
    # ==================================================================

    async def generate_null_distribution(
        self,
        db: AsyncSession,
        n_permutations: int = 1000,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Generate null distribution of composite scores via random drug-cancer pairing.

        Randomly shuffles drug-cancer assignments and recomputes scores to
        establish what scores look like under the null hypothesis (no real
        biological connection).

        Returns:
            dict with null distribution statistics and per-hypothesis p-values
        """
        result = await db.execute(
            select(
                Hypothesis.id,
                Hypothesis.composite_score,
                Hypothesis.pathway_overlap_score,
                Hypothesis.expression_correlation_score,
                Hypothesis.literature_support_score,
                Hypothesis.clinical_evidence_score,
                Hypothesis.safety_score,
                Hypothesis.novelty_score,
                Hypothesis.causal_dependency_score,
                Hypothesis.gnn_link_score,
                Hypothesis.mutation_context_score,
                Hypothesis.polypharmacology_score,
                Hypothesis.pharmacological_response_score,
            )
        )
        hypotheses = result.all()

        if len(hypotheses) < 10:
            return {"error": "Too few hypotheses for null distribution"}

        weights = await self.config.get_active_weights(db)

        # Extract dimension score vectors
        dim_vectors = {
            dim: np.array([
                getattr_from_row(h, dim) for h in hypotheses
            ])
            for dim in DIMENSIONS
        }

        rng = np.random.default_rng(seed)
        null_scores = np.zeros(n_permutations)

        for i in range(n_permutations):
            # Randomly sample one dimension score from each dimension independently
            composite = 0.0
            for dim in DIMENSIONS:
                idx = rng.integers(0, len(hypotheses))
                composite += weights[dim] * dim_vectors[dim][idx]
            null_scores[i] = min(composite, 100.0)

        # Compute per-hypothesis p-values
        observed_scores = np.array([h[1] for h in hypotheses])
        p_values = []
        for obs in observed_scores:
            p = float(np.sum(null_scores >= obs) / n_permutations)
            p = max(p, 1.0 / (n_permutations + 1))
            p_values.append(round(p, 6))

        return {
            "null_distribution": {
                "mean": round(float(null_scores.mean()), 2),
                "std": round(float(null_scores.std()), 2),
                "median": round(float(np.median(null_scores)), 2),
                "percentiles": {
                    "5th": round(float(np.percentile(null_scores, 5)), 2),
                    "25th": round(float(np.percentile(null_scores, 25)), 2),
                    "75th": round(float(np.percentile(null_scores, 75)), 2),
                    "95th": round(float(np.percentile(null_scores, 95)), 2),
                    "99th": round(float(np.percentile(null_scores, 99)), 2),
                },
                "n_permutations": n_permutations,
            },
            "hypothesis_p_values": [
                {"hypothesis_id": h[0], "score": h[1], "p_value": pv}
                for h, pv in zip(hypotheses, p_values)
            ],
            "significance_summary": {
                "n_significant_005": sum(1 for p in p_values if p < 0.05),
                "n_significant_001": sum(1 for p in p_values if p < 0.01),
                "n_total": len(p_values),
            },
        }

    # ==================================================================
    # Negative Control Validation
    # ==================================================================

    async def build_negative_controls(
        self, db: AsyncSession
    ) -> dict[str, Any]:
        """Match known repurposing failures to drug-cancer pairs in our database.

        Returns:
            dict with matched failures, unmatched failures, coverage statistics
        """
        matched = []
        unmatched = []

        for drug_fragment, cancer_fragment, failure_stage, reason in KNOWN_REPURPOSING_FAILURES:
            drug_result = await db.execute(
                select(Drug.id, Drug.name).where(
                    Drug.name.ilike(f"%{drug_fragment}%")
                ).limit(1)
            )
            drug_row = drug_result.first()

            cancer_result = await db.execute(
                select(CancerType.id, CancerType.name).where(
                    CancerType.name.ilike(f"%{cancer_fragment}%")
                ).limit(1)
            )
            cancer_row = cancer_result.first()

            if drug_row and cancer_row:
                matched.append({
                    "drug_id": drug_row[0],
                    "drug_name": drug_row[1],
                    "cancer_type_id": cancer_row[0],
                    "cancer_name": cancer_row[1],
                    "failure_stage": failure_stage,
                    "reason": reason,
                    "ground_truth_label": 0,
                })
            else:
                unmatched.append({
                    "drug_fragment": drug_fragment,
                    "cancer_fragment": cancer_fragment,
                    "failure_stage": failure_stage,
                    "reason": reason,
                    "drug_found": drug_row is not None,
                    "cancer_found": cancer_row is not None,
                })

        return {
            "matched_failures": matched,
            "unmatched_failures": unmatched,
            "n_matched": len(matched),
            "n_unmatched": len(unmatched),
            "coverage": (
                len(matched) / len(KNOWN_REPURPOSING_FAILURES)
                if KNOWN_REPURPOSING_FAILURES else 0
            ),
            "by_failure_stage": {
                stage: sum(1 for c in matched if c["failure_stage"] == stage)
                for stage in ("phase3_failed", "phase2_failed", "withdrawn")
            },
        }

    async def run_positive_negative_validation(
        self,
        db: AsyncSession,
        ground_truth: dict[str, Any] | None = None,
        negative_controls: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate using both positive (known successes) and negative (known failures) controls.

        Unlike run_retrospective_validation which treats all non-positive cases
        as negatives, this uses explicitly curated failure cases to provide
        cleaner discrimination metrics.

        Returns:
            dict with ROC-AUC, score separation, and case-level details
        """
        if ground_truth is None:
            ground_truth = await self.build_ground_truth(db)
        if negative_controls is None:
            negative_controls = await self.build_negative_controls(db)

        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"]): c
            for c in ground_truth["matched_cases"]
        }
        negative_pairs = {
            (c["drug_id"], c["cancer_type_id"]): c
            for c in negative_controls["matched_failures"]
        }

        # Remove any overlap (a drug-cancer pair in both sets)
        overlap = set(positive_pairs.keys()) & set(negative_pairs.keys())
        for pair in overlap:
            del negative_pairs[pair]

        if not positive_pairs or not negative_pairs:
            return {
                "error": "Need both positive and negative matched cases",
                "n_positive_matched": len(positive_pairs),
                "n_negative_matched": len(negative_pairs),
            }

        # Get all hypotheses and filter to known pairs
        result = await db.execute(
            select(Hypothesis).order_by(Hypothesis.composite_score.desc())
        )
        all_hypotheses = result.scalars().all()

        labels = []
        scores = []
        case_details = []

        for hyp in all_hypotheses:
            pair = (hyp.drug_id, hyp.cancer_type_id)
            if pair in positive_pairs:
                labels.append(1)
                scores.append(hyp.composite_score)
                case_details.append({
                    "drug_name": positive_pairs[pair].get("drug_name", ""),
                    "cancer_name": positive_pairs[pair].get("cancer_name", ""),
                    "composite_score": hyp.composite_score,
                    "label": "positive",
                    "evidence_level": positive_pairs[pair].get("evidence_level", ""),
                })
            elif pair in negative_pairs:
                labels.append(0)
                scores.append(hyp.composite_score)
                case_details.append({
                    "drug_name": negative_pairs[pair].get("drug_name", ""),
                    "cancer_name": negative_pairs[pair].get("cancer_name", ""),
                    "composite_score": hyp.composite_score,
                    "label": "negative",
                    "failure_stage": negative_pairs[pair].get("failure_stage", ""),
                    "reason": negative_pairs[pair].get("reason", ""),
                })

        if not labels:
            return {
                "error": "No hypotheses matched any positive or negative control pairs",
                "n_positive_pairs_in_db": len(positive_pairs),
                "n_negative_pairs_in_db": len(negative_pairs),
                "n_hypotheses_total": len(all_hypotheses),
            }

        labels_arr = np.array(labels)
        scores_arr = np.array(scores)
        n_pos = int(labels_arr.sum())
        n_neg = len(labels_arr) - n_pos

        metrics = {
            "n_positive": n_pos,
            "n_negative": n_neg,
            "n_overlap_removed": len(overlap),
        }

        if n_pos > 0 and n_neg > 0:
            # ROC-AUC on curated positive vs negative
            try:
                roc_auc_val = float(roc_auc_score(labels_arr, scores_arr))
                metrics["roc_auc"] = round(roc_auc_val, 4)
                metrics["roc_interpretation"] = _interpret_auc(roc_auc_val)
            except Exception as e:
                logger.warning("ROC-AUC on pos/neg controls failed: %s", e)

            # Score separation
            pos_scores = scores_arr[labels_arr == 1]
            neg_scores = scores_arr[labels_arr == 0]
            metrics["positive_score_mean"] = round(float(pos_scores.mean()), 2)
            metrics["positive_score_std"] = round(float(pos_scores.std()), 2)
            metrics["negative_score_mean"] = round(float(neg_scores.mean()), 2)
            metrics["negative_score_std"] = round(float(neg_scores.std()), 2)
            metrics["score_gap"] = round(
                float(pos_scores.mean() - neg_scores.mean()), 2
            )

            # Mann-Whitney U
            u_stat, u_pval = scipy_stats.mannwhitneyu(
                pos_scores, neg_scores, alternative="greater"
            )
            metrics["mann_whitney_u"] = float(u_stat)
            metrics["mann_whitney_p"] = float(u_pval)

            # Cohen's d effect size (requires >= 2 samples per group for meaningful std)
            pooled_std = np.sqrt(
                (pos_scores.std() ** 2 + neg_scores.std() ** 2) / 2
            )
            if pooled_std > 0 and len(pos_scores) >= 2 and len(neg_scores) >= 2:
                cohens_d = float(
                    (pos_scores.mean() - neg_scores.mean()) / pooled_std
                )
                metrics["cohens_d"] = round(cohens_d, 3)
                metrics["effect_size_interpretation"] = _interpret_effect_size(cohens_d)

        return {
            "metrics": metrics,
            "case_details": sorted(
                case_details, key=lambda x: x["composite_score"], reverse=True
            ),
            "positive_controls_coverage": ground_truth["coverage"],
            "negative_controls_coverage": negative_controls["coverage"],
        }

    # ==================================================================
    # Temporal Cross-Validation
    # ==================================================================

    async def run_temporal_validation(
        self, db: AsyncSession
    ) -> dict[str, Any]:
        """Temporal cross-validation: train on established cases, test on recent.

        Simulates prospective prediction by:
        1. Using pre-2010 approved drugs as the "training" set (known positives)
        2. Evaluating whether 2010+ approvals would be ranked highly

        This answers: "Would our system have predicted recent approvals
        using only knowledge of older ones?"

        Returns:
            dict with train/test split metrics and prospective AUC
        """
        established_cases = TEMPORAL_COHORTS["established"]
        recent_cases = TEMPORAL_COHORTS["recent"]

        # Match established cases to DB
        established_pairs = set()
        for drug_fragment, cancer_fragment, _ in established_cases:
            drug_result = await db.execute(
                select(Drug.id).where(
                    Drug.name.ilike(f"%{drug_fragment}%")
                ).limit(1)
            )
            drug_row = drug_result.first()
            cancer_result = await db.execute(
                select(CancerType.id).where(
                    CancerType.name.ilike(f"%{cancer_fragment}%")
                ).limit(1)
            )
            cancer_row = cancer_result.first()
            if drug_row and cancer_row:
                established_pairs.add((drug_row[0], cancer_row[0]))

        # Match recent cases to DB
        recent_pairs = set()
        for drug_fragment, cancer_fragment, _ in recent_cases:
            drug_result = await db.execute(
                select(Drug.id).where(
                    Drug.name.ilike(f"%{drug_fragment}%")
                ).limit(1)
            )
            drug_row = drug_result.first()
            cancer_result = await db.execute(
                select(CancerType.id).where(
                    CancerType.name.ilike(f"%{cancer_fragment}%")
                ).limit(1)
            )
            cancer_row = cancer_result.first()
            if drug_row and cancer_row:
                recent_pairs.add((drug_row[0], cancer_row[0]))

        if not established_pairs or not recent_pairs:
            return {
                "error": "Insufficient matched cases for temporal validation",
                "n_established_matched": len(established_pairs),
                "n_recent_matched": len(recent_pairs),
            }

        # Get all hypotheses
        result = await db.execute(
            select(Hypothesis).order_by(Hypothesis.composite_score.desc())
        )
        all_hypotheses = result.scalars().all()
        n_total = len(all_hypotheses)

        if n_total == 0:
            return {"error": "No hypotheses scored yet"}

        # Evaluate: among all hypotheses, where do recent cases rank?
        # (Training cases are treated as "known" — only recent cases are evaluated)
        recent_ranks = []

        for rank, hyp in enumerate(all_hypotheses, 1):
            pair = (hyp.drug_id, hyp.cancer_type_id)
            if pair in recent_pairs:
                recent_ranks.append(rank)

        # Labels for AUC: recent cases = positive, everything except
        # established = negative (exclude established to avoid leakage)
        test_labels = []
        test_scores = []
        for hyp in all_hypotheses:
            pair = (hyp.drug_id, hyp.cancer_type_id)
            if pair in established_pairs:
                continue  # Exclude training set from evaluation
            test_labels.append(1 if pair in recent_pairs else 0)
            test_scores.append(hyp.composite_score)

        test_labels_arr = np.array(test_labels)
        test_scores_arr = np.array(test_scores)
        n_recent_in_test = int(test_labels_arr.sum())

        metrics = {
            "n_established_matched": len(established_pairs),
            "n_recent_matched": len(recent_pairs),
            "n_recent_found_in_hypotheses": len(recent_ranks),
            "n_test_set": len(test_labels),
        }

        if recent_ranks:
            metrics["prospective_rank_recovery"] = {
                "mean_rank": round(float(np.mean(recent_ranks)), 1),
                "median_rank": round(float(np.median(recent_ranks)), 1),
                "mean_percentile": round(
                    float(np.mean([1 - r / n_total for r in recent_ranks])) * 100, 1
                ),
                "top_10pct": sum(
                    1 for r in recent_ranks if r <= n_total * 0.10
                ),
                "top_25pct": sum(
                    1 for r in recent_ranks if r <= n_total * 0.25
                ),
            }
        else:
            metrics["prospective_rank_recovery"] = None

        if n_recent_in_test > 0 and n_recent_in_test < len(test_labels):
            try:
                prospective_auc = float(
                    roc_auc_score(test_labels_arr, test_scores_arr)
                )
                metrics["prospective_roc_auc"] = round(prospective_auc, 4)
                metrics["prospective_interpretation"] = _interpret_auc(
                    prospective_auc
                )
            except Exception as e:
                logger.warning("Prospective ROC-AUC failed: %s", e)

        return {
            "temporal_split": {
                "established_era": "pre-2010",
                "recent_era": "2010+",
                "n_established_cases": len(established_cases),
                "n_recent_cases": len(recent_cases),
            },
            "metrics": metrics,
        }

    # ==================================================================
    # Benchmark Baselines
    # ==================================================================

    async def run_benchmark_baselines(
        self,
        db: AsyncSession,
        ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare full scoring model against simple baselines.

        Baselines:
        1. Random: AUC = 0.5 (theoretical)
        2. Single-dimension: each dimension alone as predictor
        3. Majority class: always predict the majority label

        Demonstrates that the multi-dimensional scoring adds value
        beyond any single signal.

        Returns:
            dict with baseline AUCs and comparison to full model
        """
        if ground_truth is None:
            ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {"error": "No ground truth cases matched"}

        result = await db.execute(select(Hypothesis))
        all_hypotheses = result.scalars().all()

        if len(all_hypotheses) < 10:
            return {"error": "Too few hypotheses for benchmarking"}

        labels = np.array([
            1 if (h.drug_id, h.cancer_type_id) in positive_pairs else 0
            for h in all_hypotheses
        ])
        n_positive = int(labels.sum())
        n_total = len(labels)

        if n_positive == 0 or n_positive == n_total:
            return {"error": "Need both positive and negative cases"}

        # Full model AUC
        full_scores = np.array([
            float(h.composite_score) if h.composite_score is not None else 0.0
            for h in all_hypotheses
        ])
        full_auc = float(roc_auc_score(labels, full_scores))

        baselines = {
            "random": {
                "auc": 0.5,
                "description": "Random prediction (theoretical baseline)",
            },
            "majority_class": {
                "auc": 0.5,
                "description": "Always predict majority class",
            },
        }

        # Single-dimension baselines
        dim_score_attrs = {
            "pathway_overlap": "pathway_overlap_score",
            "expression_correlation": "expression_correlation_score",
            "literature_support": "literature_support_score",
            "clinical_evidence": "clinical_evidence_score",
            "safety": "safety_score",
            "novelty": "novelty_score",
            "causal_dependency": "causal_dependency_score",
            "gnn_link": "gnn_link_score",
            "mutation_context": "mutation_context_score",
            "polypharmacology": "polypharmacology_score",
            "pharmacological_response": "pharmacological_response_score",
        }

        single_dim_results = {}
        for dim, attr in dim_score_attrs.items():
            dim_scores = np.array([
                float(getattr(h, attr, 0) or 0)
                for h in all_hypotheses
            ])
            # Skip if all scores are identical (no discrimination possible)
            if dim_scores.std() < 1e-6:
                single_dim_results[dim] = {
                    "auc": 0.5,
                    "description": f"All scores identical ({dim})",
                }
                continue
            try:
                dim_auc = float(roc_auc_score(labels, dim_scores))
                single_dim_results[dim] = {
                    "auc": round(dim_auc, 4),
                    "description": f"{dim} dimension alone",
                    "lift_vs_random": round(dim_auc - 0.5, 4),
                }
            except Exception:
                single_dim_results[dim] = {
                    "auc": 0.5,
                    "description": f"Failed to compute ({dim})",
                }

        baselines["single_dimensions"] = single_dim_results

        # Best single dimension
        best_dim = max(
            single_dim_results.items(),
            key=lambda x: x[1]["auc"],
        )

        return {
            "full_model": {
                "auc": round(full_auc, 4),
                "interpretation": _interpret_auc(full_auc),
            },
            "baselines": baselines,
            "comparison": {
                "full_vs_random": round(full_auc - 0.5, 4),
                "full_vs_best_single_dim": round(
                    full_auc - best_dim[1]["auc"], 4
                ),
                "best_single_dimension": best_dim[0],
                "best_single_dim_auc": best_dim[1]["auc"],
                "multi_dim_adds_value": full_auc > best_dim[1]["auc"],
            },
            "n_hypotheses": n_total,
            "n_positive": n_positive,
        }

    # ==================================================================
    # Sensitivity Analysis
    # ==================================================================

    async def run_sensitivity_analysis(
        self,
        db: AsyncSession,
        n_perturbations: int = 200,
        perturbation_scale: float = 0.1,
        seed: int = 42,
        ground_truth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assess robustness of validation metrics to weight perturbations.

        Randomly perturbs scoring weights and measures how much the
        ROC-AUC varies. A robust model should show low variance.

        Args:
            n_perturbations: Number of random weight perturbations to test
            perturbation_scale: Standard deviation of Gaussian noise added
                to each weight (as fraction of weight value)
            seed: Random seed for reproducibility
            ground_truth: Pre-built ground truth dict (avoids redundant DB queries)

        Returns:
            dict with AUC distribution under perturbation, robustness metrics
        """
        if ground_truth is None:
            ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {"error": "No ground truth cases matched"}

        result = await db.execute(select(Hypothesis))
        all_hypotheses = result.scalars().all()

        if len(all_hypotheses) < 10:
            return {"error": "Too few hypotheses for sensitivity analysis"}

        labels = np.array([
            1 if (h.drug_id, h.cancer_type_id) in positive_pairs else 0
            for h in all_hypotheses
        ])
        n_positive = int(labels.sum())
        if n_positive == 0 or n_positive == len(labels):
            return {"error": "Need both positive and negative cases"}

        weights = await self.config.get_active_weights(db)

        # Extract dimension scores for all hypotheses
        dim_score_matrix = {}
        for dim in DIMENSIONS:
            attr = f"{dim}_score"
            dim_score_matrix[dim] = np.array([
                float(getattr(h, attr, 0) or 0)
                for h in all_hypotheses
            ])

        # Baseline AUC
        baseline_scores = np.array([
            float(h.composite_score) if h.composite_score is not None else 0.0
            for h in all_hypotheses
        ])
        baseline_auc = float(roc_auc_score(labels, baseline_scores))

        rng = np.random.default_rng(seed)
        perturbed_aucs = []

        for _ in range(n_perturbations):
            # Perturb weights with Gaussian noise
            perturbed = {}
            for dim in DIMENSIONS:
                w = weights[dim]
                noise = rng.normal(0, perturbation_scale * max(w, 0.01))
                perturbed[dim] = max(w + noise, 0.0)

            # Renormalize to sum to 1
            total = sum(perturbed.values())
            if total <= 0:
                continue

            for dim in perturbed:
                perturbed[dim] /= total

            # Compute scores with perturbed weights
            perturbed_scores = np.zeros(len(all_hypotheses))
            for dim in DIMENSIONS:
                perturbed_scores += perturbed[dim] * dim_score_matrix[dim]
            perturbed_scores = np.clip(perturbed_scores, 0, 100)

            # Skip if all scores are identical (causes sklearn ValueError)
            if perturbed_scores.std() < 1e-10:
                continue

            try:
                p_auc = float(roc_auc_score(labels, perturbed_scores))
                perturbed_aucs.append(p_auc)
            except Exception:
                continue

        if not perturbed_aucs:
            return {"error": "All perturbations failed"}

        aucs = np.array(perturbed_aucs)

        return {
            "baseline_auc": round(baseline_auc, 4),
            "perturbation_config": {
                "n_perturbations": n_perturbations,
                "perturbation_scale": perturbation_scale,
                "seed": seed,
            },
            "auc_distribution": {
                "mean": round(float(aucs.mean()), 4),
                "std": round(float(aucs.std()), 4),
                "min": round(float(aucs.min()), 4),
                "max": round(float(aucs.max()), 4),
                "median": round(float(np.median(aucs)), 4),
                "percentile_5": round(float(np.percentile(aucs, 5)), 4),
                "percentile_95": round(float(np.percentile(aucs, 95)), 4),
            },
            "robustness": {
                "cv": round(float(aucs.std() / aucs.mean()), 4) if aucs.mean() > 0 else None,
                "max_degradation": round(float(baseline_auc - aucs.min()), 4),
                "pct_above_07": round(
                    float(np.sum(aucs >= 0.7) / len(aucs) * 100), 1
                ),
                "pct_above_06": round(
                    float(np.sum(aucs >= 0.6) / len(aucs) * 100), 1
                ),
                "interpretation": _interpret_robustness(
                    baseline_auc, float(aucs.std()), float(aucs.min())
                ),
            },
            "n_successful_perturbations": len(perturbed_aucs),
        }


# ===================================================================
# Helpers
# ===================================================================


def getattr_from_row(row, dim: str) -> float:
    """Extract dimension score from a SQLAlchemy row tuple."""
    dim_index = {
        "pathway_overlap": 2,
        "expression_correlation": 3,
        "literature_support": 4,
        "clinical_evidence": 5,
        "safety": 6,
        "novelty": 7,
        "causal_dependency": 8,
        "gnn_link": 9,
        "mutation_context": 10,
        "polypharmacology": 11,
        "pharmacological_response": 12,
    }
    idx = dim_index.get(dim)
    if idx is None:
        return 0.0
    val = row[idx]
    return float(val) if val is not None else 0.0


def _interpret_auc(auc_value: float) -> str:
    """Interpret ROC-AUC value with standard thresholds."""
    if auc_value >= 0.9:
        return "excellent discrimination"
    if auc_value >= 0.8:
        return "good discrimination"
    if auc_value >= 0.7:
        return "fair discrimination"
    if auc_value >= 0.6:
        return "poor discrimination"
    return "no discrimination (random)"


def _interpret_calibration(brier: float) -> str:
    """Interpret Brier score."""
    if brier < 0.05:
        return "well calibrated"
    if brier < 0.15:
        return "moderately calibrated"
    if brier < 0.25:
        return "poorly calibrated"
    return "very poorly calibrated (scores do not reflect probabilities)"


def _generate_weight_recommendation(
    ablation_results: dict[str, dict],
    current_weights: dict[str, float],
) -> str:
    """Generate a human-readable recommendation based on ablation results."""
    important = [
        (dim, data["auc_drop"])
        for dim, data in ablation_results.items()
        if data["auc_drop"] > 0.01
    ]
    important.sort(key=lambda x: x[1], reverse=True)

    harmful = [
        (dim, data["auc_drop"])
        for dim, data in ablation_results.items()
        if data["auc_drop"] < -0.01
    ]

    parts = []
    if important:
        top = important[0]
        parts.append(
            f"Most important dimension: {top[0]} (removing it drops AUC by {top[1]:.4f}). "
            f"Consider increasing its weight from {current_weights.get(top[0], 0):.2f}."
        )

    if harmful:
        worst = harmful[0]
        parts.append(
            f"Potentially harmful dimension: {worst[0]} (removing it IMPROVES AUC by "
            f"{abs(worst[1]):.4f}). Consider reducing its weight or investigating data quality."
        )

    if not important and not harmful:
        parts.append(
            "No single dimension dramatically affects performance. "
            "Consider whether the scoring model captures the right signals."
        )

    return " ".join(parts)


def _interpret_effect_size(d: float) -> str:
    """Interpret Cohen's d effect size."""
    d_abs = abs(d)
    if d_abs >= 1.2:
        return "very large effect"
    if d_abs >= 0.8:
        return "large effect"
    if d_abs >= 0.5:
        return "medium effect"
    if d_abs >= 0.2:
        return "small effect"
    return "negligible effect"


def _interpret_robustness(
    baseline_auc: float, auc_std: float, auc_min: float
) -> str:
    """Interpret sensitivity analysis robustness."""
    if auc_std < 0.02 and auc_min >= 0.65:
        return "highly robust — results stable under weight perturbation"
    if auc_std < 0.05 and auc_min >= 0.55:
        return "moderately robust — some sensitivity to weight changes"
    if auc_std < 0.10:
        return "somewhat fragile — consider validating weight selection"
    return "fragile — results highly sensitive to weight choices"
