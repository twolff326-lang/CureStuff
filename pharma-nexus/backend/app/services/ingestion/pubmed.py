"""PubMed/NCBI connector — literature mining via E-utilities API.

Three-phase ingestion progressively broadening coverage:
  Phase 1: Targeted drug-cancer pairs with pathway connections (~10K papers)
  Phase 2: Broader drug-cancer literature (~75K papers)
  Phase 3: Target-focused literature (~20K papers)

Uses BioPython Entrez module for search (esearch) and fetch (efetch).
API base: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/
Rate limit: 3 req/sec (no key) or 10 req/sec (with NCBI_API_KEY).
"""

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any

from Bio import Entrez
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cancer_type import CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer, LiteratureTarget
from app.models.pathway import PathwayTarget
from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

# Configure Entrez
Entrez.email = settings.ncbi_email or "pharma-nexus@example.com"
if settings.ncbi_api_key:
    Entrez.api_key = settings.ncbi_api_key

EFETCH_BATCH_SIZE = 200  # Max per efetch call
RATE_DELAY = 0.1 if settings.ncbi_api_key else 0.34  # seconds between requests


class PubMedConnector(BaseConnector):
    """Ingests PubMed literature in three progressive phases."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client=None,
        phase: str = "all",
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=10.0 if settings.ncbi_api_key else 3.0,
            batch_size=500,
            timeout=60.0,
            **kwargs,
        )
        self._phase = phase  # "phase1", "phase2", "phase3", or "all"
        # Caches built during ingestion
        self._drug_name_to_id: dict[str, int] = {}
        self._cancer_name_to_id: dict[str, int] = {}
        self._gene_symbol_to_target_id: dict[str, int] = {}

    def get_source_name(self) -> str:
        return "pubmed"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Run PubMed literature ingestion phases."""
        await self._build_caches(session)
        processed = 0

        if self._phase in ("all", "phase1"):
            logger.info("=== Phase 1: Targeted drug-cancer literature ===")
            count = await self._run_phase1(session)
            processed += count
            logger.info("Phase 1 complete: %d papers ingested", count)

        if self._phase in ("all", "phase2"):
            logger.info("=== Phase 2: Broader drug-cancer literature ===")
            count = await self._run_phase2(session)
            processed += count
            logger.info("Phase 2 complete: %d papers ingested", count)

        if self._phase in ("all", "phase3"):
            logger.info("=== Phase 3: Target-focused literature ===")
            count = await self._run_phase3(session)
            processed += count
            logger.info("Phase 3 complete: %d papers ingested", count)

        self._records_processed = processed
        return []

    # ------------------------------------------------------------------
    # Cache building
    # ------------------------------------------------------------------

    async def _build_caches(self, session: AsyncSession) -> None:
        """Build name→id caches for drugs, cancer types, and targets."""
        # Drug names
        result = await session.execute(
            select(Drug.id, Drug.name, Drug.generic_name)
        )
        for drug_id, name, generic_name in result.all():
            if name:
                self._drug_name_to_id[name.lower()] = drug_id
            if generic_name:
                self._drug_name_to_id[generic_name.lower()] = drug_id

        # Cancer types
        result = await session.execute(
            select(CancerType.id, CancerType.name, CancerType.tissue)
        )
        for ct_id, name, tissue in result.all():
            if name:
                self._cancer_name_to_id[name.lower()] = ct_id
            if tissue:
                self._cancer_name_to_id[tissue.lower()] = ct_id

        # Target gene symbols
        result = await session.execute(
            select(Target.id, Target.gene_symbol)
        )
        for target_id, gene_sym in result.all():
            if gene_sym:
                self._gene_symbol_to_target_id[gene_sym.upper()] = target_id

        logger.info(
            "Caches built: %d drug names, %d cancer names, %d gene symbols",
            len(self._drug_name_to_id),
            len(self._cancer_name_to_id),
            len(self._gene_symbol_to_target_id),
        )

    # ------------------------------------------------------------------
    # Phase 1: Targeted drug-cancer pairs
    # ------------------------------------------------------------------

    async def _run_phase1(self, session: AsyncSession) -> int:
        """Find literature for drug-cancer pairs with pathway connections."""
        # Find drug-cancer pairs sharing pathway targets.
        # The drug's target must share a pathway with a gene that is altered
        # in the cancer type's molecular profile.
        pairs_query = text("""
            SELECT DISTINCT d.id AS drug_id, d.name AS drug_name,
                   ct.id AS cancer_type_id, ct.name AS cancer_name,
                   ct.tissue
            FROM drugs d
            JOIN drug_targets dt ON dt.drug_id = d.id
            JOIN pathway_targets pt ON pt.target_id = dt.target_id
            JOIN pathway_targets pt2 ON pt2.pathway_id = pt.pathway_id
            JOIN targets t2 ON t2.id = pt2.target_id
            JOIN cancer_molecular_profiles cmp ON cmp.gene_symbol = t2.gene_symbol
            JOIN cancer_types ct ON ct.id = cmp.cancer_type_id
            LIMIT 500
        """)

        result = await session.execute(pairs_query)
        pairs = result.all()
        logger.info("Phase 1: found %d drug-cancer pairs with pathway connections", len(pairs))

        processed = 0
        for drug_id, drug_name, cancer_type_id, cancer_name, tissue in pairs:
            try:
                tissue_term = f"{tissue} cancer" if tissue else ""
                cancer_terms = f'"{cancer_name}"[Title/Abstract]'
                if tissue_term:
                    cancer_terms += f' OR "{tissue_term}"[Title/Abstract]'

                query = (
                    f'"{drug_name}"[Title/Abstract] AND '
                    f"({cancer_terms}) AND "
                    f"(repurposing OR repositioning OR \"off-label\" OR "
                    f"anticancer OR antitumor OR \"anti-cancer\" OR \"anti-tumor\")"
                )

                pmids = await self._search_pubmed(query, retmax=50)
                if pmids:
                    count = await self._fetch_and_store_papers(
                        session, pmids,
                        drug_id=drug_id, cancer_type_id=cancer_type_id,
                        mention_type="primary_subject",
                    )
                    processed += count

                if processed % 500 == 0 and processed > 0:
                    await session.commit()
                    logger.info("Phase 1 progress: %d papers", processed)

            except Exception as exc:
                self.record_error(
                    f"phase1_{drug_name}_{cancer_name}", exc,
                    record_id=f"{drug_id}_{cancer_type_id}",
                )

        await session.commit()
        return processed

    # ------------------------------------------------------------------
    # Phase 2: Broader drug-cancer literature
    # ------------------------------------------------------------------

    async def _run_phase2(self, session: AsyncSession) -> int:
        """Find broader literature for each drug in cancer contexts."""
        result = await session.execute(
            select(Drug.id, Drug.name).where(Drug.name.isnot(None))
        )
        drugs = result.all()
        logger.info("Phase 2: searching %d drugs for cancer literature", len(drugs))

        processed = 0
        for drug_id, drug_name in drugs:
            try:
                query = (
                    f'"{drug_name}"[Title/Abstract] AND '
                    f"(cancer OR tumor OR neoplasm OR carcinoma OR oncology OR malignant)"
                )

                pmids = await self._search_pubmed(
                    query, retmax=100, mindate="2010", maxdate="2026"
                )
                if pmids:
                    count = await self._fetch_and_store_papers(
                        session, pmids,
                        drug_id=drug_id,
                        mention_type="primary_subject",
                    )
                    processed += count

                if processed % 500 == 0 and processed > 0:
                    await session.commit()
                    logger.info("Phase 2 progress: %d papers", processed)

            except Exception as exc:
                self.record_error(f"phase2_{drug_name}", exc, record_id=str(drug_id))

        await session.commit()
        return processed

    # ------------------------------------------------------------------
    # Phase 3: Target-focused literature
    # ------------------------------------------------------------------

    # Short gene symbols that are also common English words — require
    # the full gene name to be present in the query to avoid false positives.
    _AMBIGUOUS_GENE_SYMBOLS: set[str] = {
        "MET", "RET", "SET", "CIC", "SRC", "FOS", "JUN", "KIT", "RAN",
        "CAD", "GAS", "ACE", "ARC", "BAD", "BAG", "BAP", "BIN", "CAN",
        "CAP", "DAB", "DAM", "DIS", "FAT", "GAP", "HIP", "MAX", "MAD",
        "MAP", "NET", "NOR", "PAK", "PAX", "PER", "PIN", "PIT", "RAD",
        "RAP", "SAG", "SEC", "SHE", "SKI", "SOS", "TAB", "TIP", "TOP",
        "WAS",
    }

    async def _run_phase3(self, session: AsyncSession) -> int:
        """Find literature for top drug targets in cancer contexts."""
        # Get top 1000 most connected targets (with gene_name for disambiguation)
        result = await session.execute(
            select(Target.id, Target.gene_symbol, Target.gene_name)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .group_by(Target.id, Target.gene_symbol, Target.gene_name)
            .order_by(func.count(DrugTarget.id).desc())
            .limit(1000)
        )
        targets = result.all()
        logger.info("Phase 3: searching %d targets for cancer literature", len(targets))

        processed = 0
        for target_id, gene_symbol, gene_name in targets:
            if not gene_symbol:
                continue

            # Skip ambiguous short symbols unless we have a gene name to disambiguate
            if gene_symbol.upper() in self._AMBIGUOUS_GENE_SYMBOLS:
                if gene_name:
                    # Use the full gene name instead of the symbol
                    gene_term = f'"{gene_name}"[Title/Abstract]'
                else:
                    continue
            else:
                gene_term = f"{gene_symbol}[Title/Abstract]"

            try:
                query = (
                    f"{gene_term} AND "
                    f"(cancer OR tumor) AND "
                    f"(therapeutic OR treatment OR inhibitor OR drug OR target)"
                )

                pmids = await self._search_pubmed(
                    query, retmax=50, mindate="2015", maxdate="2026"
                )
                if pmids:
                    count = await self._fetch_and_store_papers(
                        session, pmids,
                        target_id=target_id,
                        mention_type="primary_subject",
                    )
                    processed += count

                if processed % 500 == 0 and processed > 0:
                    await session.commit()
                    logger.info("Phase 3 progress: %d papers", processed)

            except Exception as exc:
                self.record_error(
                    f"phase3_{gene_symbol}", exc, record_id=str(target_id)
                )

        await session.commit()
        return processed

    # ------------------------------------------------------------------
    # E-utilities wrappers (run in executor to avoid blocking)
    # ------------------------------------------------------------------

    async def _search_pubmed(
        self,
        query: str,
        retmax: int = 100,
        mindate: str | None = None,
        maxdate: str | None = None,
    ) -> list[str]:
        """Search PubMed via esearch, return list of PMIDs."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_running_loop()

        def _do_search():
            kwargs: dict[str, Any] = {
                "db": "pubmed",
                "term": query,
                "retmax": retmax,
                "sort": "relevance",
            }
            if mindate:
                kwargs["mindate"] = mindate
            if maxdate:
                kwargs["maxdate"] = maxdate

            handle = Entrez.esearch(**kwargs)
            results = Entrez.read(handle)
            handle.close()
            return results.get("IdList", [])

        try:
            return await loop.run_in_executor(None, _do_search)
        except Exception as exc:
            logger.warning("PubMed search failed: %s (query: %.80s...)", exc, query)
            return []

    async def _fetch_papers(
        self, pmids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch full PubMed records via efetch in batches of 200."""
        all_records: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()

        for i in range(0, len(pmids), EFETCH_BATCH_SIZE):
            batch = pmids[i : i + EFETCH_BATCH_SIZE]
            await self._rate_limiter.acquire()

            def _do_fetch(batch_ids=batch):
                handle = Entrez.efetch(
                    db="pubmed", id=",".join(batch_ids), rettype="xml"
                )
                records = Entrez.read(handle)
                handle.close()
                return records

            try:
                raw = await loop.run_in_executor(None, _do_fetch)
                articles = raw.get("PubmedArticle", [])
                for article in articles:
                    parsed = self._parse_pubmed_record(article)
                    if parsed:
                        all_records.append(parsed)
            except Exception as exc:
                logger.warning(
                    "efetch failed for batch %d-%d: %s",
                    i, i + len(batch), exc,
                )

        return all_records

    # ------------------------------------------------------------------
    # Record parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pubmed_record(article: dict) -> dict[str, Any] | None:
        """Parse a single PubmedArticle dict into our schema."""
        try:
            citation = article.get("MedlineCitation", {})
            pmid = str(citation.get("PMID", ""))
            if not pmid:
                return None

            art = citation.get("Article", {})

            # Title
            title = str(art.get("ArticleTitle", ""))

            # Abstract — may be list of labeled sections or a single string
            abstract_parts = (
                art.get("Abstract", {}).get("AbstractText", [])
            )
            if isinstance(abstract_parts, list):
                sections = []
                for part in abstract_parts:
                    label = ""
                    if hasattr(part, "attributes"):
                        label = part.attributes.get("Label", "")
                    text_val = str(part)
                    if label:
                        sections.append(f"{label.upper()}: {text_val}")
                    else:
                        sections.append(text_val)
                abstract = " ".join(sections)
            else:
                abstract = str(abstract_parts) if abstract_parts else ""

            # Authors
            authors = []
            author_list = art.get("AuthorList", [])
            for author in author_list:
                if isinstance(author, dict):
                    authors.append({
                        "lastname": author.get("LastName", ""),
                        "forename": author.get("ForeName", ""),
                    })

            # Journal
            journal_info = art.get("Journal", {})
            journal = str(journal_info.get("Title", ""))

            # Publication date
            pub_date = None
            pub_date_dict = (
                journal_info.get("JournalIssue", {}).get("PubDate", {})
            )
            year = pub_date_dict.get("Year", "")
            month = pub_date_dict.get("Month", "01")
            day = pub_date_dict.get("Day", "01")
            if year:
                try:
                    # Month might be abbreviated name
                    month_map = {
                        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
                        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
                        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
                    }
                    m = month_map.get(str(month), None)
                    if m is None:
                        m = int(month) if str(month).isdigit() else 1
                    d = int(day) if str(day).isdigit() else 1
                    pub_date = date(int(year), m, d)
                except (ValueError, TypeError):
                    pub_date = date(int(year), 1, 1) if str(year).isdigit() else None

            # DOI
            doi = ""
            elocation_ids = art.get("ELocationID", [])
            if isinstance(elocation_ids, list):
                for eid in elocation_ids:
                    if hasattr(eid, "attributes") and eid.attributes.get("EIdType") == "doi":
                        doi = str(eid)
                        break
            elif hasattr(elocation_ids, "attributes") and elocation_ids.attributes.get("EIdType") == "doi":
                doi = str(elocation_ids)

            # MeSH terms
            mesh_terms = []
            mesh_list = citation.get("MeshHeadingList", [])
            for mesh in mesh_list:
                descriptor = mesh.get("DescriptorName", "")
                qualifiers = mesh.get("QualifierName", [])
                if descriptor:
                    desc_str = str(descriptor)
                    if qualifiers:
                        for qual in qualifiers:
                            mesh_terms.append(f"{desc_str}/{str(qual)}")
                    else:
                        mesh_terms.append(desc_str)

            return {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "pub_date": pub_date,
                "doi": doi,
                "mesh_terms": mesh_terms,
            }

        except Exception as exc:
            logger.debug("Failed to parse PubMed record: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def _fetch_and_store_papers(
        self,
        session: AsyncSession,
        pmids: list[str],
        drug_id: int | None = None,
        cancer_type_id: int | None = None,
        target_id: int | None = None,
        mention_type: str = "primary_subject",
    ) -> int:
        """Fetch papers by PMID and store with junction records."""
        records = await self._fetch_papers(pmids)
        if not records:
            return 0

        stored = 0
        for record in records:
            try:
                # Derive relevance tags from MeSH terms
                relevance_tags = self._derive_relevance_tags(record.get("mesh_terms", []))

                # Upsert literature record
                stmt = pg_insert(Literature.__table__).values(
                    pmid=record["pmid"],
                    title=record["title"],
                    abstract=record["abstract"],
                    authors=record["authors"],
                    journal=record["journal"],
                    pub_date=record["pub_date"],
                    doi=record["doi"],
                    mesh_terms=record["mesh_terms"],
                    relevance_tags=relevance_tags,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["pmid"],
                    set_={
                        "title": stmt.excluded.title,
                        "abstract": stmt.excluded.abstract,
                        "authors": stmt.excluded.authors,
                        "journal": stmt.excluded.journal,
                        "pub_date": stmt.excluded.pub_date,
                        "doi": stmt.excluded.doi,
                        "mesh_terms": stmt.excluded.mesh_terms,
                        "relevance_tags": stmt.excluded.relevance_tags,
                    },
                )
                await session.execute(stmt)
                await session.flush()

                # Get the literature ID
                lit_result = await session.execute(
                    select(Literature.id).where(
                        Literature.pmid == record["pmid"]
                    )
                )
                lit_id = lit_result.scalar_one()

                # Create primary junction records
                if drug_id:
                    await self._upsert_literature_drug(
                        session, lit_id, drug_id, mention_type
                    )

                if cancer_type_id:
                    await self._upsert_literature_cancer(
                        session, lit_id, cancer_type_id, mention_type
                    )

                if target_id:
                    await self._upsert_literature_target(
                        session, lit_id, target_id, mention_type
                    )

                # Scan abstract for secondary mentions
                abstract_text = record.get("abstract", "") or ""
                title_text = record.get("title", "") or ""
                full_text = f"{title_text} {abstract_text}".lower()

                await self._scan_secondary_mentions(
                    session, lit_id, full_text,
                    primary_drug_id=drug_id,
                    primary_cancer_id=cancer_type_id,
                )

                stored += 1

            except Exception as exc:
                logger.debug(
                    "Failed to store paper PMID %s: %s",
                    record.get("pmid", "?"), exc,
                )

        return stored

    async def _scan_secondary_mentions(
        self,
        session: AsyncSession,
        lit_id: int,
        full_text: str,
        primary_drug_id: int | None = None,
        primary_cancer_id: int | None = None,
    ) -> None:
        """Scan abstract text for mentions of other drugs and cancer types."""
        # Scan for drug mentions
        for drug_name, drug_id in self._drug_name_to_id.items():
            if drug_id == primary_drug_id:
                continue
            if len(drug_name) < 4:
                continue  # Skip short names to avoid false positives
            if drug_name in full_text:
                await self._upsert_literature_drug(
                    session, lit_id, drug_id, "secondary"
                )

        # Scan for cancer type mentions
        for cancer_name, ct_id in self._cancer_name_to_id.items():
            if ct_id == primary_cancer_id:
                continue
            if len(cancer_name) < 4:
                continue
            if cancer_name in full_text:
                await self._upsert_literature_cancer(
                    session, lit_id, ct_id, "secondary"
                )

    # ------------------------------------------------------------------
    # Junction record upserts
    # ------------------------------------------------------------------

    @staticmethod
    async def _upsert_literature_drug(
        session: AsyncSession, lit_id: int, drug_id: int, mention_type: str
    ) -> None:
        stmt = pg_insert(LiteratureDrug.__table__).values(
            literature_id=lit_id, drug_id=drug_id, mention_type=mention_type
        )
        # Never downgrade: primary_subject > secondary > passing
        stmt = stmt.on_conflict_do_update(
            index_elements=["literature_id", "drug_id"],
            set_={"mention_type": text(
                "CASE WHEN literature_drugs.mention_type = 'primary_subject' "
                "THEN 'primary_subject' ELSE EXCLUDED.mention_type END"
            )},
        )
        await session.execute(stmt)

    @staticmethod
    async def _upsert_literature_target(
        session: AsyncSession, lit_id: int, target_id: int, mention_type: str
    ) -> None:
        stmt = pg_insert(LiteratureTarget.__table__).values(
            literature_id=lit_id, target_id=target_id, mention_type=mention_type
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["literature_id", "target_id"],
            set_={"mention_type": text(
                "CASE WHEN literature_targets.mention_type = 'primary_subject' "
                "THEN 'primary_subject' ELSE EXCLUDED.mention_type END"
            )},
        )
        await session.execute(stmt)

    @staticmethod
    async def _upsert_literature_cancer(
        session: AsyncSession, lit_id: int, cancer_type_id: int, mention_type: str
    ) -> None:
        stmt = pg_insert(LiteratureCancer.__table__).values(
            literature_id=lit_id, cancer_type_id=cancer_type_id,
            mention_type=mention_type,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["literature_id", "cancer_type_id"],
            set_={"mention_type": text(
                "CASE WHEN literature_cancers.mention_type = 'primary_subject' "
                "THEN 'primary_subject' ELSE EXCLUDED.mention_type END"
            )},
        )
        await session.execute(stmt)

    # ------------------------------------------------------------------
    # Relevance tagging
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_relevance_tags(mesh_terms: list[str]) -> list[str]:
        """Derive relevance tags from MeSH terms for filtering.

        Categories: drug_repurposing, clinical_trial, mechanism,
        biomarker, resistance, combination_therapy, review
        """
        tags: set[str] = set()
        mesh_lower = [m.lower() for m in mesh_terms]
        mesh_joined = " ".join(mesh_lower)

        if any(k in mesh_joined for k in [
            "drug repositioning", "drug repurposing", "off-label use",
        ]):
            tags.add("drug_repurposing")

        if any(k in mesh_joined for k in [
            "clinical trial", "randomized controlled trial",
            "controlled clinical trial",
        ]):
            tags.add("clinical_trial")

        if any(k in mesh_joined for k in [
            "drug resistance", "antineoplastic", "multidrug resistance",
        ]):
            tags.add("resistance")

        if any(k in mesh_joined for k in [
            "biomarker", "tumor marker", "prognosis",
        ]):
            tags.add("biomarker")

        if any(k in mesh_joined for k in [
            "signal transduction", "apoptosis", "cell proliferation",
            "molecular targeted therapy", "protein kinase",
        ]):
            tags.add("mechanism")

        if any(k in mesh_joined for k in [
            "drug therapy, combination", "combined modality therapy",
            "drug synergism",
        ]):
            tags.add("combination_therapy")

        if any(k in mesh_joined for k in [
            "review", "meta-analysis", "systematic review",
        ]):
            tags.add("review")

        return sorted(tags)
