"""COSMIC connector — Cancer Gene Census and driver mutation data.

Supports two modes:
  - Mode A (preferred): Parse Cancer Gene Census TSV file (requires download)
  - Mode B (fallback): Annotate known cancer driver genes

COSMIC data enriches our mutation records with:
  - Cancer driver gene annotations
  - COSMIC mutation IDs
  - Functional impact classifications (marking drivers as 'high' impact)

COSMIC: https://cancer.sanger.ac.uk/cosmic/
"""

import csv
import logging
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.mutation import Mutation
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

# Well-known cancer driver genes for Mode B fallback
KNOWN_CANCER_DRIVERS = [
    "TP53", "KRAS", "PIK3CA", "BRAF", "PTEN", "APC", "EGFR", "RB1",
    "BRCA1", "BRCA2", "MYC", "CDKN2A", "NRAS", "ATM", "VHL", "SMAD4",
    "KMT2D", "ARID1A", "KMT2C", "NF1", "CTNNB1", "IDH1", "IDH2",
    "ERBB2", "ALK", "RET", "FGFR2", "FGFR3", "JAK2", "NPM1",
    "FLT3", "KIT", "PDGFRA", "MET", "ABL1", "NOTCH1", "FBXW7",
    "CREBBP", "EP300", "SETD2", "BAP1", "STAG2", "DNMT3A", "TET2",
    "SF3B1", "U2AF1", "SRSF2", "ASXL1", "EZH2", "SUZ12",
    "SMARCA4", "SMARCB1", "GATA3", "FOXA1", "CDH1", "MAP2K1",
    "MAP3K1", "SPOP", "MTOR", "TSC1", "TSC2", "STK11", "KEAP1",
    "NFE2L2", "CIC", "FUBP1", "ATRX", "DAXX", "H3F3A", "HIST1H3B",
]


class COSMICConnector(BaseConnector):
    """Ingests cancer gene census and driver mutation data from COSMIC."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        census_tsv_path: str | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=1.0,  # COSMIC rate limits are strict
            batch_size=500,
            **kwargs,
        )
        self._census_tsv_path = census_tsv_path
        self._headers = {"Accept": "application/json"}

    def get_source_name(self) -> str:
        return "cosmic"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch COSMIC data: Mode A (TSV) or Mode B (known drivers fallback)."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            processed = 0

            if self._census_tsv_path and Path(self._census_tsv_path).exists():
                # Mode A: Parse Cancer Gene Census TSV
                logger.info(
                    "COSMIC Mode A: parsing Census TSV from %s",
                    self._census_tsv_path,
                )
                processed = await self._parse_cancer_gene_census(session)
            else:
                # Mode B: Use known driver gene list
                logger.info("COSMIC Mode B: annotating known cancer driver genes")
                processed = await self._annotate_known_drivers(session)

            self._records_processed = processed
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Mode A: Cancer Gene Census TSV
    # ------------------------------------------------------------------

    async def _parse_cancer_gene_census(self, session: AsyncSession) -> int:
        """Parse the Cancer Gene Census TSV file.

        Expected columns include:
            Gene Symbol, Name, Entrez GeneId, Genome Location,
            Tier, Hallmark, Chr Band, Somatic, Germline,
            Tumour Types(Somatic), Tumour Types(Germline),
            Cancer Syndrome, Tissue Type, Molecular Genetics,
            Role in Cancer, Mutation Types, Translocation Partner,
            Other Germline Mut, Other Syndrome, Synonyms
        """
        path = Path(self._census_tsv_path)

        with open(path, "r", encoding="utf-8") as f:
            # Detect delimiter (TSV or CSV)
            sample = f.read(2048)
            f.seek(0)
            delimiter = "\t" if "\t" in sample else ","

            reader = csv.DictReader(f, delimiter=delimiter)

            gene_records: list[dict[str, Any]] = []
            for row in reader:
                gene_symbol = row.get("Gene Symbol", "").strip()
                if not gene_symbol:
                    continue

                tier = row.get("Tier", "").strip()
                role = row.get("Role in Cancer", "").strip()
                tumour_types_somatic = row.get("Tumour Types(Somatic)", "").strip()
                mutation_types = row.get("Mutation Types", "").strip()
                hallmark = row.get("Hallmark", "").strip()

                gene_records.append({
                    "gene_symbol": gene_symbol,
                    "tier": tier,
                    "role_in_cancer": role,
                    "tumour_types_somatic": tumour_types_somatic,
                    "mutation_types": mutation_types,
                    "is_hallmark": hallmark.lower() == "yes" if hallmark else False,
                    "is_somatic": row.get("Somatic", "").strip().lower() == "yes",
                    "is_germline": row.get("Germline", "").strip().lower() == "yes",
                })

        processed = 0

        # Mark existing mutations from these genes as cancer drivers
        processed += await self._mark_driver_genes(
            session, [g["gene_symbol"] for g in gene_records]
        )

        # Create molecular profile entries for driver genes across cancer types
        processed += await self._create_driver_profiles(session, gene_records)

        await session.commit()
        return processed

    # ------------------------------------------------------------------
    # Mode B: Known cancer drivers fallback
    # ------------------------------------------------------------------

    async def _annotate_known_drivers(self, session: AsyncSession) -> int:
        """Annotate mutations for known cancer driver genes."""
        await self.set_total_expected(session, len(KNOWN_CANCER_DRIVERS))

        processed = 0

        # Mark known driver mutations
        marked = await self._mark_driver_genes(session, KNOWN_CANCER_DRIVERS)
        processed += marked
        if marked == 0:
            logger.warning(
                "COSMIC: 0 mutations marked as drivers — cBioPortal/TCGA "
                "ingestion must run first to populate the mutations table"
            )

        # Create driver profiles from the known list
        gene_records = [
            {
                "gene_symbol": gene,
                "tier": "1",
                "role_in_cancer": "oncogene/TSG",
                "tumour_types_somatic": "",
                "mutation_types": "",
                "is_hallmark": False,
                "is_somatic": True,
                "is_germline": False,
            }
            for gene in KNOWN_CANCER_DRIVERS
        ]
        profiles = await self._create_driver_profiles(session, gene_records)
        processed += profiles
        if profiles == 0:
            logger.warning(
                "COSMIC: 0 driver profiles created — ensure cancer types "
                "and mutations exist (run cBioPortal + TCGA ingestion first)"
            )

        await session.commit()
        self._records_processed = processed
        await self._flush_progress(session)
        return processed

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _mark_driver_genes(
        self, session: AsyncSession, gene_symbols: list[str]
    ) -> int:
        """Mark existing mutations for these genes as having high functional impact."""
        if not gene_symbols:
            return 0

        updated = 0
        batch_size = 100
        for i in range(0, len(gene_symbols), batch_size):
            batch = gene_symbols[i : i + batch_size]
            stmt = (
                update(Mutation)
                .where(
                    Mutation.gene_symbol.in_(batch),
                    Mutation.source != "cosmic",
                )
                .values(functional_impact="high")
            )
            result = await session.execute(stmt)
            updated += result.rowcount

        logger.info("Marked %d mutations as driver genes", updated)
        return updated

    async def _create_driver_profiles(
        self,
        session: AsyncSession,
        gene_records: list[dict[str, Any]],
    ) -> int:
        """Create CancerMolecularProfile entries for driver genes across cancer types."""
        # Get all cancer types
        result = await session.execute(select(CancerType.id, CancerType.tcga_code))
        cancer_types = result.all()

        if not cancer_types:
            logger.warning(
                "No cancer types found — run cBioPortal/TCGA ingestion first"
            )
            return 0

        # Build a set of genes that have mutations in each cancer type
        gene_symbols = [g["gene_symbol"] for g in gene_records]

        # Batch query: get all mutation frequencies for driver genes
        mut_result = await session.execute(
            select(
                Mutation.cancer_type_id,
                Mutation.gene_symbol,
                Mutation.frequency_percent,
            ).where(Mutation.gene_symbol.in_(gene_symbols))
        )
        mutation_map: dict[tuple[int, str], float] = {}
        for ct_id, gene, freq in mut_result.all():
            if freq is not None and freq > 0:
                mutation_map[(ct_id, gene)] = freq

        profile_records = []
        for (ct_id, gene), freq in mutation_map.items():
            profile_records.append({
                "cancer_type_id": ct_id,
                "gene_symbol": gene,
                "alteration_type": "driver_mutation",
                "frequency_percent": freq,
                "median_expression": None,
                "expression_zscore": None,
                "source": "cosmic",
            })

        if not profile_records:
            return 0

        count = await self.batch_upsert_composite(
            session,
            CancerMolecularProfile,
            profile_records,
            conflict_columns=["cancer_type_id", "gene_symbol", "alteration_type"],
            update_columns=["frequency_percent", "source"],
        )
        logger.info("Created %d COSMIC driver gene profiles", count)
        return count
