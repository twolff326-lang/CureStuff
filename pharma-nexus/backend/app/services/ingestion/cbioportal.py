"""cBioPortal connector — primary source for TCGA cancer genomics data.

Fetches via the cBioPortal REST API (no auth required):
  - Cancer types from TCGA studies
  - Mutation data (top 500 mutated genes per cancer type)
  - Gene expression z-scores (summary stats for top 2000 variable genes)
  - Copy-number alteration frequencies (amplification/deletion > 2%)

API docs: https://www.cbioportal.org/api
"""

import logging
import math
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.evidence import GeneExpression
from app.models.mutation import Mutation
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

API_BASE = "https://www.cbioportal.org/api"

# Limits
MAX_GENES_MUTATIONS = 500
MAX_GENES_EXPRESSION = 2000
CNA_FREQUENCY_THRESHOLD = 2.0  # Only store genes altered in ≥ 2% of samples
SAMPLE_PAGE_SIZE = 10000


class CBioPortalConnector(BaseConnector):
    """Ingests TCGA cancer genomics data from cBioPortal."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=5.0,  # cBioPortal is generous
            batch_size=500,
            **kwargs,
        )
        self._headers = {
            "Accept": "application/json",
        }
        # Cache study → molecular profiles
        self._profile_cache: dict[str, dict[str, str]] = {}

    def get_source_name(self) -> str:
        return "cbioportal"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — this connector writes directly in fetch_data phases."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Multi-phase ingestion: cancer types → mutations → expression → CNA."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Phase 1: Discover TCGA studies & upsert cancer types
            studies = await self._fetch_tcga_studies(client)
            cancer_type_map = await self._upsert_cancer_types(session, studies)
            logger.info("Phase 1 complete: %d TCGA cancer types", len(cancer_type_map))

            # Cache molecular profile IDs per study
            await self._cache_molecular_profiles(client, studies)

            total_studies = len(cancer_type_map)
            await self.set_total_expected(session, total_studies)
            processed = total_studies

            # Phase 2–4: Process each cancer type
            for idx, (study_id, ct_id) in enumerate(cancer_type_map.items(), 1):
                tcga_code = study_id.replace("_tcga", "").upper()
                logger.info("Processing cancer type: %s (study=%s) [%d/%d]", tcga_code, study_id, idx, total_studies)

                try:
                    # Phase 2: Mutations
                    mut_count = await self._fetch_mutations(
                        client, session, study_id, ct_id
                    )
                    processed += mut_count

                    # Phase 3: Expression summary
                    expr_count = await self._fetch_expression(
                        client, session, study_id, ct_id
                    )
                    processed += expr_count

                    # Phase 4: CNA
                    cna_count = await self._fetch_cna(
                        client, session, study_id, ct_id
                    )
                    processed += cna_count

                    await session.commit()

                except Exception as exc:
                    self.record_error(
                        f"cancer_type_{tcga_code}", exc, record_id=study_id
                    )
                    await session.rollback()

                # Flush progress after each study
                self._records_processed = idx
                await self._flush_progress(session)

            self._records_processed = processed
            await self._flush_progress(session)
            return []  # Already stored inline

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Phase 1: TCGA studies → cancer_types
    # ------------------------------------------------------------------

    async def _fetch_tcga_studies(
        self, client: httpx.AsyncClient
    ) -> list[dict[str, Any]]:
        """Fetch all TCGA studies from cBioPortal."""
        resp = await self.http_get(
            client,
            f"{API_BASE}/studies",
            params={"projection": "DETAILED", "pageSize": 1000},
            headers=self._headers,
        )
        all_studies = resp.json()

        # Filter to TCGA studies (studyId ends with "_tcga")
        tcga_studies = [
            s for s in all_studies
            if s.get("studyId", "").endswith("_tcga")
        ]
        logger.info(
            "Found %d TCGA studies out of %d total",
            len(tcga_studies), len(all_studies),
        )
        return tcga_studies

    async def _upsert_cancer_types(
        self,
        session: AsyncSession,
        studies: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Upsert cancer types from TCGA studies. Returns {study_id: cancer_type.id}."""
        records = []
        for study in studies:
            study_id = study["studyId"]
            tcga_code = study_id.replace("_tcga", "").upper()

            cancer_type_detail = study.get("cancerType", {})
            records.append({
                "tcga_code": tcga_code,
                "name": study.get("name", tcga_code),
                "tissue": cancer_type_detail.get("dedicatedColor", ""),
                "organ": cancer_type_detail.get("name", ""),
                "subtype": cancer_type_detail.get("cancerTypeId", ""),
                "sample_count": study.get("allSampleCount", 0),
                "description": study.get("description", ""),
            })

        await self.batch_upsert(
            session,
            CancerType,
            records,
            conflict_column="tcga_code",
            update_columns=["name", "tissue", "organ", "subtype", "sample_count", "description"],
        )
        await session.commit()

        # Build map: study_id -> cancer_type.id
        result = await session.execute(select(CancerType.tcga_code, CancerType.id))
        tcga_id_map = {row[0]: row[1] for row in result.all()}

        return {
            s["studyId"]: tcga_id_map[s["studyId"].replace("_tcga", "").upper()]
            for s in studies
            if s["studyId"].replace("_tcga", "").upper() in tcga_id_map
        }

    # ------------------------------------------------------------------
    # Molecular profile discovery
    # ------------------------------------------------------------------

    async def _cache_molecular_profiles(
        self, client: httpx.AsyncClient, studies: list[dict[str, Any]]
    ) -> None:
        """Cache molecular profile IDs for each study.

        Profiles of interest: mutations, mrna_seq_v2_rna_seq (expression), gistic (CNA).
        """
        for study in studies:
            study_id = study["studyId"]
            try:
                resp = await self.http_get(
                    client,
                    f"{API_BASE}/studies/{study_id}/molecular-profiles",
                    headers=self._headers,
                )
                profiles = resp.json()

                profile_map: dict[str, str] = {}
                for p in profiles:
                    mol_type = p.get("molecularAlterationType", "")
                    data_type = p.get("datatype", "")
                    pid = p["molecularProfileId"]

                    if mol_type == "MUTATION_EXTENDED":
                        profile_map["mutations"] = pid
                    elif mol_type == "MRNA_EXPRESSION" and "z-scores" in p.get("name", "").lower():
                        profile_map["expression"] = pid
                    elif mol_type == "MRNA_EXPRESSION" and "expression" not in profile_map:
                        profile_map["expression"] = pid
                    elif mol_type == "COPY_NUMBER_ALTERATION" and data_type == "DISCRETE":
                        profile_map["cna"] = pid

                self._profile_cache[study_id] = profile_map

            except Exception as exc:
                self.record_error(
                    f"profiles_{study_id}", exc, record_id=study_id
                )
                self._profile_cache[study_id] = {}

    # ------------------------------------------------------------------
    # Phase 2: Mutations
    # ------------------------------------------------------------------

    async def _fetch_mutations(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        study_id: str,
        cancer_type_id: int,
    ) -> int:
        """Fetch mutation data for a cancer type: top 500 mutated genes."""
        profile_id = self._profile_cache.get(study_id, {}).get("mutations")
        if not profile_id:
            logger.debug("No mutation profile for %s", study_id)
            return 0

        # Get sample list
        sample_ids = await self._get_sample_ids(client, study_id)
        if not sample_ids:
            return 0

        total_samples = len(sample_ids)

        # Fetch mutations using the molecular profile — paginated
        all_mutations: list[dict] = []
        page_size = 10000
        page = 0
        while True:
            try:
                resp = await self.http_get(
                    client,
                    f"{API_BASE}/molecular-profiles/{profile_id}/mutations",
                    params={
                        "sampleListId": f"{study_id}_all",
                        "projection": "DETAILED",
                        "pageSize": page_size,
                        "pageNumber": page,
                    },
                    headers=self._headers,
                )
                batch = resp.json()
                if not batch:
                    break
                all_mutations.extend(batch)
                if len(batch) < page_size:
                    break
                page += 1
            except Exception as exc:
                self.record_error(
                    f"mutations_page_{page}", exc, record_id=study_id
                )
                break

        if not all_mutations:
            return 0

        # Aggregate: count mutations per gene + protein change
        gene_mutation_counts: dict[str, dict[str, Any]] = {}
        for m in all_mutations:
            gene = m.get("gene", {}).get("hugoGeneSymbol", "")
            if not gene:
                continue

            key = (gene, m.get("proteinChange", ""), m.get("mutationType", ""))
            if key not in gene_mutation_counts:
                gene_mutation_counts[key] = {
                    "gene_symbol": gene,
                    "protein_change": m.get("proteinChange", ""),
                    "mutation_type": m.get("mutationType", "unknown"),
                    "chr": m.get("chr", ""),
                    "start": m.get("startPosition", ""),
                    "end": m.get("endPosition", ""),
                    "functional_impact": m.get("functionalImpactScore", "unknown"),
                    "samples": set(),
                }
            gene_mutation_counts[key]["samples"].add(
                m.get("sampleId", "")
            )

        # Build mutation records — top 500 by frequency
        sorted_mutations = sorted(
            gene_mutation_counts.values(),
            key=lambda x: len(x["samples"]),
            reverse=True,
        )[:MAX_GENES_MUTATIONS]

        mutation_records = []
        for mut in sorted_mutations:
            freq = (len(mut["samples"]) / total_samples * 100) if total_samples > 0 else 0.0
            genomic_pos = ""
            if mut["chr"] and mut["start"]:
                genomic_pos = f"chr{mut['chr']}:{mut['start']}"
                if mut["end"] and mut["end"] != mut["start"]:
                    genomic_pos += f"-{mut['end']}"

            mutation_records.append({
                "cancer_type_id": cancer_type_id,
                "gene_symbol": mut["gene_symbol"],
                "mutation_type": mut["mutation_type"] or "unknown",
                "protein_change": mut["protein_change"] or None,
                "genomic_position": genomic_pos or None,
                "frequency_percent": round(freq, 4),
                "functional_impact": mut["functional_impact"] or "unknown",
                "cosmic_id": None,
                "source": "cbioportal",
            })

        # The mutations table has no unique constraint on
        # (cancer_type_id, gene_symbol) — multiple sources and protein
        # changes coexist.  Delete existing cBioPortal data for this
        # cancer type, then bulk-insert fresh records.
        from sqlalchemy import delete
        await session.execute(
            delete(Mutation).where(
                Mutation.cancer_type_id == cancer_type_id,
                Mutation.source == "cbioportal",
            )
        )

        count = await self.batch_insert_no_conflict(
            session, Mutation, mutation_records,
        )
        logger.info(
            "%s: stored %d mutations (from %d raw)",
            study_id, count, len(all_mutations),
        )
        return count

    # ------------------------------------------------------------------
    # Phase 3: Gene expression summary
    # ------------------------------------------------------------------

    async def _fetch_expression(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        study_id: str,
        cancer_type_id: int,
    ) -> int:
        """Fetch expression z-scores for top variable genes → store summaries."""
        profile_id = self._profile_cache.get(study_id, {}).get("expression")
        if not profile_id:
            logger.debug("No expression profile for %s", study_id)
            return 0

        sample_ids = await self._get_sample_ids(client, study_id)
        if not sample_ids:
            return 0

        # Fetch gene panel or all genes — we request the top variable genes
        # by fetching molecular data for a large set of common cancer genes
        # then computing variance
        try:
            # Get all genes' data for a subset of samples to identify variable genes
            resp = await self.http_get(
                client,
                f"{API_BASE}/molecular-profiles/{profile_id}/molecular-data",
                params={
                    "sampleListId": f"{study_id}_all",
                    "projection": "SUMMARY",
                    "pageSize": 50000,
                    "pageNumber": 0,
                },
                headers=self._headers,
            )
            data = resp.json()
        except Exception as exc:
            self.record_error(f"expression_{study_id}", exc, record_id=study_id)
            return 0

        if not data:
            return 0

        # Aggregate per gene: collect values, compute summary stats
        gene_values: dict[str, list[float]] = {}
        for entry in data:
            gene = entry.get("gene", {}).get("hugoGeneSymbol", "")
            value = entry.get("value")
            if gene and value is not None and not (isinstance(value, float) and math.isnan(value)):
                gene_values.setdefault(gene, []).append(float(value))

        # Compute summary stats per gene and sort by variance (top 2000)
        gene_stats: list[dict[str, Any]] = []
        for gene, values in gene_values.items():
            if len(values) < 3:
                continue
            mean_val = sum(values) / len(values)
            variance = sum((v - mean_val) ** 2 for v in values) / len(values)
            median_val = sorted(values)[len(values) // 2]
            gene_stats.append({
                "gene_symbol": gene,
                "median_expression": round(median_val, 4),
                "expression_zscore": round(mean_val, 4),
                "variance": variance,
                "n_samples": len(values),
            })

        gene_stats.sort(key=lambda x: x["variance"], reverse=True)
        top_genes = gene_stats[:MAX_GENES_EXPRESSION]

        # Store as CancerMolecularProfile records
        profile_records = []
        for gs in top_genes:
            profile_records.append({
                "cancer_type_id": cancer_type_id,
                "gene_symbol": gs["gene_symbol"],
                "alteration_type": "expression",
                "frequency_percent": None,
                "median_expression": gs["median_expression"],
                "expression_zscore": gs["expression_zscore"],
                "source": "cbioportal",
            })

        count = await self.batch_upsert_composite(
            session,
            CancerMolecularProfile,
            profile_records,
            conflict_columns=["cancer_type_id", "gene_symbol", "alteration_type"],
            update_columns=[
                "median_expression", "expression_zscore", "source",
            ],
        )
        logger.info(
            "%s: stored %d expression profiles (top variable genes)",
            study_id, count,
        )
        return count

    # ------------------------------------------------------------------
    # Phase 4: Copy number alterations
    # ------------------------------------------------------------------

    async def _fetch_cna(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        study_id: str,
        cancer_type_id: int,
    ) -> int:
        """Fetch discrete CNA data and compute amplification/deletion frequencies."""
        profile_id = self._profile_cache.get(study_id, {}).get("cna")
        if not profile_id:
            logger.debug("No CNA profile for %s", study_id)
            return 0

        sample_ids = await self._get_sample_ids(client, study_id)
        if not sample_ids:
            return 0

        total_samples = len(sample_ids)

        try:
            all_cna: list[dict] = []
            page = 0
            while True:
                resp = await self.http_get(
                    client,
                    f"{API_BASE}/molecular-profiles/{profile_id}/discrete-copy-number",
                    params={
                        "sampleListId": f"{study_id}_all",
                        "projection": "SUMMARY",
                        "pageSize": 50000,
                        "pageNumber": page,
                    },
                    headers=self._headers,
                )
                batch = resp.json()
                if not batch:
                    break
                all_cna.extend(batch)
                if len(batch) < 50000:
                    break
                page += 1

        except Exception as exc:
            self.record_error(f"cna_{study_id}", exc, record_id=study_id)
            return 0

        if not all_cna:
            return 0

        # Aggregate: count amp (value=2) and del (value=-2) per gene
        gene_alterations: dict[str, dict[str, set]] = {}
        for entry in all_cna:
            gene = entry.get("gene", {}).get("hugoGeneSymbol", "")
            value = entry.get("alteration")
            sample = entry.get("sampleId", "")
            if not gene or value is None:
                continue

            if gene not in gene_alterations:
                gene_alterations[gene] = {"amp": set(), "del": set()}

            if value == 2:  # Amplification
                gene_alterations[gene]["amp"].add(sample)
            elif value == -2:  # Deep deletion
                gene_alterations[gene]["del"].add(sample)

        # Build profile records for genes above threshold
        cna_records = []
        for gene, alts in gene_alterations.items():
            amp_freq = len(alts["amp"]) / total_samples * 100 if total_samples > 0 else 0
            del_freq = len(alts["del"]) / total_samples * 100 if total_samples > 0 else 0

            if amp_freq >= CNA_FREQUENCY_THRESHOLD:
                cna_records.append({
                    "cancer_type_id": cancer_type_id,
                    "gene_symbol": gene,
                    "alteration_type": "amplification",
                    "frequency_percent": round(amp_freq, 4),
                    "median_expression": None,
                    "expression_zscore": None,
                    "source": "cbioportal",
                })
            if del_freq >= CNA_FREQUENCY_THRESHOLD:
                cna_records.append({
                    "cancer_type_id": cancer_type_id,
                    "gene_symbol": gene,
                    "alteration_type": "deletion",
                    "frequency_percent": round(del_freq, 4),
                    "median_expression": None,
                    "expression_zscore": None,
                    "source": "cbioportal",
                })

        if not cna_records:
            return 0

        count = await self.batch_upsert_composite(
            session,
            CancerMolecularProfile,
            cna_records,
            conflict_columns=["cancer_type_id", "gene_symbol", "alteration_type"],
            update_columns=[
                "frequency_percent", "source",
            ],
        )
        logger.info(
            "%s: stored %d CNA profiles (>%.1f%% frequency)",
            study_id, count, CNA_FREQUENCY_THRESHOLD,
        )
        return count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_sample_ids(
        self, client: httpx.AsyncClient, study_id: str
    ) -> list[str]:
        """Fetch all sample IDs for a study."""
        try:
            resp = await self.http_get(
                client,
                f"{API_BASE}/studies/{study_id}/samples",
                params={"projection": "ID", "pageSize": SAMPLE_PAGE_SIZE},
                headers=self._headers,
            )
            samples = resp.json()
            return [s["sampleId"] for s in samples]
        except Exception as exc:
            self.record_error(f"samples_{study_id}", exc, record_id=study_id)
            return []
