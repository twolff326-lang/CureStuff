import pytest
from unittest.mock import MagicMock


class MockResult:
    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar = scalar_value

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class MockSession:
    def __init__(self):
        self._results: list[MockResult] = []
        self._idx = 0
        self._added = []

    def queue_result(self, result: MockResult):
        self._results.append(result)

    async def execute(self, stmt, *args, **kwargs):
        if self._idx < len(self._results):
            r = self._results[self._idx]
            self._idx += 1
            return r
        return MockResult()

    def add(self, obj):
        self._added.append(obj)

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture
def mock_db():
    return MockSession()


@pytest.fixture
def sample_drug():
    return {
        "name": "Imatinib",
        "generic_name": "imatinib mesylate",
        "pubchem_cid": 5291,
        "chembl_id": "CHEMBL941",
        "status": "approved",
    }


@pytest.fixture
def sample_cancer_type():
    return {
        "name": "Chronic Myeloid Leukemia",
        "tcga_code": "LAML",
        "tissue": "Blood",
    }
