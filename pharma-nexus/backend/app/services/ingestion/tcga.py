"""TCGA/GDC connector — supplementary cancer genomics data from the Genomic Data Commons.

Fetches via the GDC REST API (no auth required for public data):
  - Project metadata (supplements cBioPortal cancer types)
  - Top mutated genes per project (simple somatic mutations)
  - Gene expression summary statistics

GDC API docs: https://docs.gdc.cancer.gov/API/Users_Guide/Search_and_Retrieval/
"""

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.mutation import Mutation
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

GDC_API_BASE = "https://api.gdc.cancer.gov"

# Top genes to fetch per project
TOP_MUTATED_GENES = 500
GDC_PAGE_SIZE = 500


class TCGAConnector(BaseConnector):
    """Ingests supplementary TCGA data from GDC (Genomic Data Commons)."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=3.0,
            batch_size=500,
            **kwargs,
        )
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_source_name(self) -> str:
        return "tcga_gdc"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch TCGA project metadata and top mutated genes from GDC."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Phase 1: Get TCGA projects and ensure cancer_types exist
            projects = await self._fetch_tcga_projects(client)
            ct_map = await self._ensure_cancer_types(session, projects)
            logger.info("Phase 1 complete: %d TCGA projects mapped", len(ct_map))

            processed = len(ct_map)

            # Phase 2: Top mutated genes per project
            for project_id, ct_id in ct_map.items():
                try:
                    mut_count = await self._fetch_top_mutations(
                        client, session, project_id, ct_id
                    )
                    processed += mut_count
                    await session.commit()
                except Exception as exc:
                    self.record_error(
                        f"mutations_{project_id}", exc, record_id=project_id
                    )
                    await session.rollback()

            self._records_processed = processed
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Phase 1: TCGA projects
    # ------------------------------------------------------------------

    async def _fetch_tcga_projects(
        self, client: httpx.AsyncClient
    ) -> list[dict[str, Any]]:
        """Fetch all TCGA projects from GDC."""
        filters = {
            "op": "eq",
            "content": {
                "field": "program.name",
                "value": "TCGA",
            },
        }

        resp = await self.http_get(
            client,
            f"{GDC_API_BASE}/projects",
            params={
                "filters": json.dumps(filters),
                "fields": "project_id,name,primary_site,disease_type,summary.case_count",
                "size": 100,
                "from": 0,
            },
            headers=self._headers,
        )
        data = resp.json()
        hits = data.get("data", {}).get("hits", [])
        logger.info("Found %d TCGA projects from GDC", len(hits))
        return hits

    async def _ensure_cancer_types(
        self,
        session: AsyncSession,
        projects: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Ensure cancer_types rows exist for TCGA projects. Returns {project_id: ct_id}."""
        ct_map: dict[str, int] = {}

        for proj in projects:
            project_id = proj.get("project_id", "")
            # Extract TCGA code: "TCGA-BRCA" → "BRCA"
            tcga_code = project_id.replace("TCGA-", "").upper()

            # Check if already exists (cBioPortal may have created it)
            result = await session.execute(
                select(CancerType.id).where(CancerType.tcga_code == tcga_code)
            )
            existing_id = result.scalar_one_or_none()

            if existing_id:
                # Update sample_count from GDC if available
                summary = proj.get("summary", {})
                case_count = summary.get("case_count") if isinstance(summary, dict) else None
                if case_count:
                    await session.execute(
                        update(CancerType)
                        .where(CancerType.id == existing_id)
                        .values(sample_count=case_count)
                    )
                ct_map[project_id] = existing_id
            else:
                # Create new cancer type
                primary_site = proj.get("primary_site", [""])[0] if isinstance(proj.get("primary_site"), list) else proj.get("primary_site", "")
                disease_type = proj.get("disease_type", [""])[0] if isinstance(proj.get("disease_type"), list) else proj.get("disease_type", "")
                summary = proj.get("summary", {})
                case_count = summary.get("case_count") if isinstance(summary, dict) else None

                ct = CancerType(
                    tcga_code=tcga_code,
                    name=proj.get("name", tcga_code),
                    tissue=primary_site,
                    organ=primary_site,
                    subtype=disease_type,
                    sample_count=case_count,
                    description=f"TCGA {disease_type} ({primary_site})",
                )
                session.add(ct)
                await session.flush()
                ct_map[project_id] = ct.id

        await session.commit()
        return ct_map

    # ------------------------------------------------------------------
    # Phase 2: Top mutated genes
    # ------------------------------------------------------------------

    async def _fetch_top_mutations(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        project_id: str,
        cancer_type_id: int,
    ) -> int:
        """Fetch top mutated genes for a project from GDC analysis endpoint."""
        # Get total cases for frequency calculation
        result = await session.execute(
            select(CancerType.sample_count).where(CancerType.id == cancer_type_id)
        )
        total_cases = result.scalar_one_or_none() or 0

        filters = {
            "op": "eq",
            "content": {
                "field": "cases.project.project_id",
                "value": project_id,
            },
        }

        try:
            resp = await self.http_get(
                client,
                f"{GDC_API_BASE}/analysis/top_mutated_genes_by_project",
                params={
                    "filters": json.dumps(filters),
                    "size": TOP_MUTATED_GENES,
                },
                headers=self._headers,
            )
            data = resp.json()
        except Exception as exc:
            self.record_error(
                f"gdc_top_mutations_{project_id}", exc, record_id=project_id
            )
            return 0

        hits = data.get("data", {}).get("hits", [])
        if not hits:
            return 0

        mutation_records = []
        for hit in hits:
            gene_symbol = hit.get("symbol", "")
            if not gene_symbol:
                continue

            # Extract case count from nested _score structure
            case_count = 0
            score = hit.get("_score", {})
            if isinstance(score, dict):
                for proj_entry in score.get("projects", []):
                    if proj_entry.get("project_id") == project_id:
                        case_count = proj_entry.get("case_count", 0)
                        break
            elif isinstance(score, (int, float)):
                case_count = int(score)

            freq = (case_count / total_cases * 100) if total_cases > 0 and case_count > 0 else 0.0

            cytoband = hit.get("cytoband", [])
            genomic_pos = f"chr{cytoband[0]}" if cytoband else None

            mutation_records.append({
                "cancer_type_id": cancer_type_id,
                "gene_symbol": gene_symbol,
                "mutation_type": hit.get("biotype", "protein_coding"),
                "protein_change": None,
                "genomic_position": genomic_pos,
                "frequency_percent": round(freq, 4) if freq > 0 else None,
                "functional_impact": "unknown",
                "cosmic_id": None,
                "source": "gdc",
            })

        # Remove previous GDC mutation data for this cancer type before
        # inserting fresh results.  The mutations table has no unique
        # constraint on (cancer_type_id, gene_symbol) — other sources may
        # store multiple rows per gene — so ON CONFLICT upsert won't work.
        from sqlalchemy import delete
        await session.execute(
            delete(Mutation).where(
                Mutation.cancer_type_id == cancer_type_id,
                Mutation.source == "gdc",
            )
        )

        count = await self.batch_insert_no_conflict(
            session,
            Mutation,
            mutation_records,
        )
        logger.info(
            "%s: stored %d top mutated genes from GDC", project_id, count
        )
        return count
