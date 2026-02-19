"""Shared test fixtures for pharma-nexus backend tests.

Provides mock async sessions, fake model instances, and reusable
test data for all test modules.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Create a shared event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Mock async database session
# ---------------------------------------------------------------------------

class MockResult:
    """Mimics SQLAlchemy result objects."""

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar = scalar_value

    def scalars(self):
        return self

    def all(self):
        if self._rows is not None:
            return self._rows
        return []

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class MockSession:
    """Async mock that behaves like AsyncSession for testing."""

    def __init__(self):
        self._execute_map: list[MockResult] = []
        self._execute_index = 0
        self._added = []
        self._deleted = []
        self._committed = False
        self._flushed = False

    def queue_result(self, result: MockResult):
        """Queue a result for the next execute() call."""
        self._execute_map.append(result)

    async def execute(self, stmt, *args, **kwargs):
        if self._execute_index < len(self._execute_map):
            result = self._execute_map[self._execute_index]
            self._execute_index += 1
            return result
        return MockResult()

    def add(self, obj):
        self._added.append(obj)

    async def delete(self, obj):
        self._deleted.append(obj)

    async def flush(self):
        self._flushed = True

    async def commit(self):
        self._committed = True

    def reset(self):
        self._execute_index = 0
        self._execute_map.clear()
        self._added.clear()
        self._deleted.clear()
        self._committed = False
        self._flushed = False


@pytest.fixture
def mock_db():
    """Provide a mock async database session."""
    return MockSession()


# ---------------------------------------------------------------------------
# Fake model instances
# ---------------------------------------------------------------------------

def make_drug(
    id: int = 1,
    drugbank_id: str = "DB00001",
    name: str = "TestDrug",
    status: str = "approved",
    mechanism_of_action: str = "Inhibits target X",
    indication: str = "Treatment of cancer",
    **kwargs,
) -> MagicMock:
    drug = MagicMock()
    drug.id = id
    drug.drugbank_id = drugbank_id
    drug.name = name
    drug.status = status
    drug.mechanism_of_action = mechanism_of_action
    drug.indication = indication
    for k, v in kwargs.items():
        setattr(drug, k, v)
    return drug


def make_cancer_type(
    id: int = 1,
    name: str = "Breast Cancer",
    tcga_code: str = "BRCA",
    **kwargs,
) -> MagicMock:
    ct = MagicMock()
    ct.id = id
    ct.name = name
    ct.tcga_code = tcga_code
    for k, v in kwargs.items():
        setattr(ct, k, v)
    return ct


def make_literature(
    id: int = 1,
    pmid: str = "12345678",
    title: str = "Test paper on drug repurposing for breast cancer treatment approaches",
    abstract: str = "Abstract text",
    journal: str = "Nature",
    pub_date: None = None,
    relevance_tags: list | None = None,
    extracted_findings: dict | None = None,
    analysis_status: str = "pending",
) -> MagicMock:
    lit = MagicMock()
    lit.id = id
    lit.pmid = pmid
    lit.title = title
    lit.abstract = abstract
    lit.journal = journal
    lit.pub_date = pub_date
    lit.relevance_tags = relevance_tags or []
    lit.extracted_findings = extracted_findings
    lit.analysis_status = analysis_status
    return lit


def make_clinical_trial(
    id: int = 1,
    nct_id: str = "NCT00000001",
    title: str = "Phase 2 trial of TestDrug in Breast Cancer",
    status: str = "completed",
    phase: str = "Phase 2",
    conditions: list | None = None,
    enrollment: int = 100,
) -> MagicMock:
    trial = MagicMock()
    trial.id = id
    trial.nct_id = nct_id
    trial.title = title
    trial.status = status
    trial.phase = phase
    trial.conditions = conditions or ["Breast Cancer"]
    trial.enrollment = enrollment
    return trial


def make_expression_cache(
    score: float = 65.0,
    details: dict | None = None,
    cache_type: str = "drug_expression",
) -> MagicMock:
    cache = MagicMock()
    cache.score = score
    cache.details = details or {
        "target_scores": [
            {
                "gene_symbol": "EGFR",
                "action_type": "inhibitor",
                "zscore": 2.5,
                "compatibility": 0.83,
            }
        ]
    }
    cache.cache_type = cache_type
    return cache


def make_hypothesis(
    id: int = 1,
    drug_id: int = 1,
    cancer_type_id: int = 1,
    composite_score: float = 55.0,
    evidence_strength: str = "moderate",
    pathway_overlap_score: float = 40.0,
    expression_correlation_score: float = 60.0,
    literature_support_score: float = 50.0,
    clinical_evidence_score: float = 30.0,
    safety_score: float = 70.0,
    novelty_score: float = 80.0,
    **kwargs,
) -> MagicMock:
    h = MagicMock()
    h.id = id
    h.drug_id = drug_id
    h.cancer_type_id = cancer_type_id
    h.composite_score = composite_score
    h.evidence_strength = evidence_strength
    h.pathway_overlap_score = pathway_overlap_score
    h.expression_correlation_score = expression_correlation_score
    h.literature_support_score = literature_support_score
    h.clinical_evidence_score = clinical_evidence_score
    h.safety_score = safety_score
    h.novelty_score = novelty_score
    for k, v in kwargs.items():
        setattr(h, k, v)
    return h


# ---------------------------------------------------------------------------
# Pathway data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pathway_data_shared():
    """Pre-computed pathway overlap data for testing."""
    return {
        "shared_pathways": [
            {
                "pathway_id": 1,
                "pathway_name": "PI3K-Akt signaling",
                "overlap_significance": 0.85,
                "drug_targets_in_pathway": ["EGFR", "PIK3CA"],
                "cancer_altered_genes_in_pathway": ["PIK3CA", "AKT1"],
            },
            {
                "pathway_id": 2,
                "pathway_name": "MAPK signaling",
                "overlap_significance": 0.6,
                "drug_targets_in_pathway": ["BRAF"],
                "cancer_altered_genes_in_pathway": ["KRAS", "NRAS"],
            },
        ],
        "shared_count": 2,
        "total_drug_target_pathways": 5,
        "total_cancer_altered_pathways": 8,
    }


@pytest.fixture
def pathway_data_empty():
    """Pathway data with no overlap."""
    return {
        "shared_pathways": [],
        "shared_count": 0,
        "total_drug_target_pathways": 3,
        "total_cancer_altered_pathways": 5,
    }


@pytest.fixture
def pathway_data_high_overlap():
    """Pathway data with very high overlap and direct target match."""
    return {
        "shared_pathways": [
            {
                "pathway_id": 1,
                "pathway_name": "Cell cycle",
                "overlap_significance": 0.95,
                "drug_targets_in_pathway": ["CDK4", "CDK6"],
                "cancer_altered_genes_in_pathway": ["CDK4", "RB1"],
            },
            {
                "pathway_id": 2,
                "pathway_name": "Apoptosis",
                "overlap_significance": 0.8,
                "drug_targets_in_pathway": ["BCL2"],
                "cancer_altered_genes_in_pathway": ["BCL2", "BAX"],
            },
            {
                "pathway_id": 3,
                "pathway_name": "p53 signaling",
                "overlap_significance": 0.75,
                "drug_targets_in_pathway": ["MDM2"],
                "cancer_altered_genes_in_pathway": ["TP53", "MDM2"],
            },
        ],
        "shared_count": 3,
        "total_drug_target_pathways": 4,
        "total_cancer_altered_pathways": 4,
    }
