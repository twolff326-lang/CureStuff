"""Neo4j knowledge graph builder service.

Reads entities and relationships from PostgreSQL and creates/updates
nodes and edges in Neo4j. Uses MERGE + UNWIND for idempotent batch operations.

Node types: Drug, Target, Pathway, CancerType, Gene, Paper, ClinicalTrial, Mutation
Edge types: TARGETS, PARTICIPATES_IN, IS_TARGET, MUTATED_IN, OVEREXPRESSED_IN,
            UNDEREXPRESSED_IN, AMPLIFIED_IN, DELETED_IN, INTERACTS_WITH,
            MENTIONS_DRUG, STUDIES_CANCER, MENTIONS_TARGET, TESTS_DRUG,
            TARGETS_CANCER, ASSOCIATED_WITH

Expected totals: ~90,000 nodes, ~435,000 edges.
"""

import logging
from typing import Any

from neo4j import AsyncGraphDatabase
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, LiteratureDrug, TrialDrug
from app.models.literature import Literature, LiteratureCancer, LiteratureTarget
from app.models.mutation import Mutation
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import ProteinInteraction, Target
from app.models.target_disease import TargetDiseaseAssociation
from app.services.ingestion.clinicaltrials import CONDITION_TO_TCGA

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


class KnowledgeGraphService:
    """Builds and syncs the Neo4j knowledge graph from PostgreSQL data."""

    def __init__(self):
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=50,
        )

    async def close(self):
        await self.driver.close()

    async def _run_write(self, query: str, parameters: dict | None = None):
        """Execute a write transaction."""
        async with self.driver.session() as session:
            await session.run(query, parameters or {})

    async def _run_write_batched(
        self, query: str, items: list[dict], param_name: str = "batch"
    ) -> int:
        """Execute a UNWIND query in batches of BATCH_SIZE."""
        total = 0
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i : i + BATCH_SIZE]
            try:
                async with self.driver.session() as session:
                    await session.run(query, {param_name: batch})
                total += len(batch)
            except Exception:
                logger.error("Neo4j batch write failed at offset %d/%d", i, len(items))
                raise
        return total

    # ==================================================================
    # Schema initialization
    # ==================================================================

    async def init_schema(self):
        """Create constraints and indexes. Idempotent."""
        constraints = [
            "CREATE CONSTRAINT drug_drugbank_id IF NOT EXISTS FOR (d:Drug) REQUIRE d.drugbank_id IS UNIQUE",
            "CREATE CONSTRAINT target_uniprot_id IF NOT EXISTS FOR (t:Target) REQUIRE t.uniprot_id IS UNIQUE",
            "CREATE CONSTRAINT pathway_external_id IF NOT EXISTS FOR (p:Pathway) REQUIRE p.external_id IS UNIQUE",
            "CREATE CONSTRAINT cancer_type_tcga_code IF NOT EXISTS FOR (c:CancerType) REQUIRE c.tcga_code IS UNIQUE",
            "CREATE CONSTRAINT gene_symbol_unique IF NOT EXISTS FOR (g:Gene) REQUIRE g.symbol IS UNIQUE",
            "CREATE CONSTRAINT paper_pmid IF NOT EXISTS FOR (p:Paper) REQUIRE p.pmid IS UNIQUE",
            "CREATE CONSTRAINT clinical_trial_nct_id IF NOT EXISTS FOR (ct:ClinicalTrial) REQUIRE ct.nct_id IS UNIQUE",
            "CREATE CONSTRAINT mutation_pg_id IF NOT EXISTS FOR (m:Mutation) REQUIRE m.pg_id IS UNIQUE",
        ]
        indexes = [
            "CREATE INDEX drug_name_index IF NOT EXISTS FOR (d:Drug) ON (d.name)",
            "CREATE INDEX target_gene_symbol_index IF NOT EXISTS FOR (t:Target) ON (t.gene_symbol)",
            "CREATE INDEX gene_symbol_index IF NOT EXISTS FOR (g:Gene) ON (g.symbol)",
            "CREATE INDEX cancer_type_name_index IF NOT EXISTS FOR (c:CancerType) ON (c.name)",
            "CREATE INDEX pathway_name_index IF NOT EXISTS FOR (p:Pathway) ON (p.name)",
            "CREATE INDEX paper_pub_year_index IF NOT EXISTS FOR (p:Paper) ON (p.pub_year)",
            "CREATE INDEX trial_phase_index IF NOT EXISTS FOR (ct:ClinicalTrial) ON (ct.phase)",
            "CREATE INDEX trial_status_index IF NOT EXISTS FOR (ct:ClinicalTrial) ON (ct.status)",
        ]
        for stmt in constraints + indexes:
            try:
                await self._run_write(stmt)
            except Exception as exc:
                logger.debug("Schema statement skipped: %s", exc)

        logger.info("Neo4j schema initialized (%d constraints, %d indexes)",
                     len(constraints), len(indexes))

    # ==================================================================
    # Node sync methods
    # ==================================================================

    async def sync_drugs(self, db_session: AsyncSession) -> int:
        """Create/update Drug nodes."""
        result = await db_session.execute(
            select(
                Drug.id, Drug.drugbank_id, Drug.name,
                Drug.mechanism_of_action, Drug.status, Drug.indication,
            )
        )
        items = [
            {
                "pg_id": r[0], "drugbank_id": r[1], "name": r[2],
                "mechanism_of_action": (r[3] or "")[:500],
                "status": r[4], "indication": (r[5] or "")[:500],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (d:Drug {drugbank_id: item.drugbank_id})
        SET d.name = item.name,
            d.mechanism_of_action = item.mechanism_of_action,
            d.status = item.status,
            d.indication = item.indication,
            d.pg_id = item.pg_id
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Drug nodes", count)
        return count

    async def sync_targets(self, db_session: AsyncSession) -> int:
        """Create/update Target nodes (only those with UniProt IDs)."""
        result = await db_session.execute(
            select(
                Target.id, Target.uniprot_id, Target.gene_symbol,
                Target.gene_name, Target.protein_class,
                Target.function_description,
            ).where(Target.uniprot_id.isnot(None))
        )
        items = [
            {
                "pg_id": r[0], "uniprot_id": r[1], "gene_symbol": r[2],
                "gene_name": r[3], "protein_class": r[4],
                "function_summary": (r[5] or "")[:500],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (t:Target {uniprot_id: item.uniprot_id})
        SET t.gene_symbol = item.gene_symbol,
            t.gene_name = item.gene_name,
            t.protein_class = item.protein_class,
            t.function_summary = item.function_summary,
            t.pg_id = item.pg_id
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Target nodes", count)
        return count

    async def sync_pathways(self, db_session: AsyncSession) -> int:
        """Create/update Pathway nodes."""
        result = await db_session.execute(
            select(
                Pathway.id, Pathway.external_id, Pathway.name,
                Pathway.source, Pathway.category,
            )
        )
        items = [
            {
                "pg_id": r[0], "external_id": r[1], "name": r[2],
                "source": r[3], "category": r[4],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (p:Pathway {external_id: item.external_id})
        SET p.name = item.name,
            p.source = item.source,
            p.category = item.category,
            p.pg_id = item.pg_id
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Pathway nodes", count)
        return count

    async def sync_cancer_types(self, db_session: AsyncSession) -> int:
        """Create/update CancerType nodes."""
        result = await db_session.execute(
            select(
                CancerType.id, CancerType.tcga_code, CancerType.name,
                CancerType.tissue, CancerType.organ, CancerType.sample_count,
            )
        )
        items = [
            {
                "pg_id": r[0], "tcga_code": r[1], "name": r[2],
                "tissue": r[3], "organ": r[4], "sample_count": r[5],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (c:CancerType {tcga_code: item.tcga_code})
        SET c.name = item.name,
            c.tissue = item.tissue,
            c.organ = item.organ,
            c.sample_count = item.sample_count,
            c.pg_id = item.pg_id
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d CancerType nodes", count)
        return count

    async def sync_genes(self, db_session: AsyncSession) -> int:
        """Create Gene nodes for all gene symbols across pathways, mutations, and profiles.

        Also creates IS_TARGET edges linking Gene nodes to their Target counterparts.
        """
        # Collect unique gene symbols from multiple sources
        gene_symbols: set[str] = set()

        # From pathway JSONB genes arrays
        pathway_result = await db_session.execute(
            select(Pathway.genes).where(Pathway.genes.isnot(None))
        )
        for (genes_json,) in pathway_result.all():
            if isinstance(genes_json, list):
                gene_symbols.update(str(g) for g in genes_json if g)

        # From cancer molecular profiles
        cmp_result = await db_session.execute(
            select(CancerMolecularProfile.gene_symbol).distinct()
        )
        gene_symbols.update(r[0] for r in cmp_result.all() if r[0])

        # From mutations
        mut_result = await db_session.execute(
            select(Mutation.gene_symbol).distinct()
        )
        gene_symbols.update(r[0] for r in mut_result.all() if r[0])

        # From targets (to ensure Gene <-> Target linkage)
        target_result = await db_session.execute(
            select(Target.gene_symbol).where(Target.gene_symbol.isnot(None))
        )
        gene_symbols.update(r[0] for r in target_result.all() if r[0])

        items = [{"symbol": sym, "name": sym} for sym in gene_symbols if sym]
        query = """
        UNWIND $batch AS item
        MERGE (g:Gene {symbol: item.symbol})
        SET g.name = item.name
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Gene nodes", count)

        # Link Gene <-> Target where both exist
        link_query = """
        MATCH (g:Gene)
        MATCH (t:Target {gene_symbol: g.symbol})
        MERGE (g)-[:IS_TARGET]->(t)
        """
        await self._run_write(link_query)
        logger.info("Linked Gene-Target IS_TARGET edges")

        return count

    async def sync_papers(self, db_session: AsyncSession) -> int:
        """Create Paper nodes for literature with at least one drug or cancer link."""
        result = await db_session.execute(text("""
            SELECT DISTINCT l.id, l.pmid, l.title, l.journal,
                   EXTRACT(YEAR FROM l.pub_date)::int AS pub_year,
                   l.analysis_status,
                   CASE WHEN l.extracted_findings IS NOT NULL
                        THEN l.extracted_findings->>'study_type'
                        ELSE NULL END AS study_type
            FROM literature l
            WHERE EXISTS (
                SELECT 1 FROM literature_drugs ld WHERE ld.literature_id = l.id
            ) OR EXISTS (
                SELECT 1 FROM literature_cancers lc WHERE lc.literature_id = l.id
            )
        """))
        items = [
            {
                "pg_id": r[0], "pmid": r[1],
                "title": (r[2] or "")[:300],
                "journal": r[3], "pub_year": r[4],
                "study_type": r[6],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (p:Paper {pmid: item.pmid})
        SET p.title = item.title,
            p.journal = item.journal,
            p.pub_year = item.pub_year,
            p.study_type = item.study_type,
            p.pg_id = item.pg_id
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Paper nodes", count)
        return count

    async def sync_clinical_trials(self, db_session: AsyncSession) -> int:
        """Create ClinicalTrial nodes."""
        result = await db_session.execute(
            select(
                ClinicalTrial.id, ClinicalTrial.nct_id, ClinicalTrial.title,
                ClinicalTrial.phase, ClinicalTrial.status,
                ClinicalTrial.enrollment,
            )
        )
        items = [
            {
                "pg_id": r[0], "nct_id": r[1],
                "title": (r[2] or "")[:300],
                "phase": r[3], "status": r[4], "enrollment": r[5],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (ct:ClinicalTrial {nct_id: item.nct_id})
        SET ct.title = item.title,
            ct.phase = item.phase,
            ct.status = item.status,
            ct.enrollment = item.enrollment,
            ct.pg_id = item.pg_id
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d ClinicalTrial nodes", count)
        return count

    async def sync_mutations(self, db_session: AsyncSession) -> int:
        """Create Mutation nodes."""
        result = await db_session.execute(
            select(
                Mutation.id, Mutation.gene_symbol, Mutation.mutation_type,
                Mutation.protein_change, Mutation.frequency_percent,
                Mutation.functional_impact,
            )
        )
        items = [
            {
                "pg_id": r[0], "gene_symbol": r[1], "mutation_type": r[2],
                "protein_change": r[3], "frequency_percent": r[4],
                "functional_impact": r[5],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS item
        MERGE (m:Mutation {pg_id: item.pg_id})
        SET m.gene_symbol = item.gene_symbol,
            m.mutation_type = item.mutation_type,
            m.protein_change = item.protein_change,
            m.frequency_percent = item.frequency_percent,
            m.functional_impact = item.functional_impact
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Mutation nodes", count)
        return count

    # ==================================================================
    # Relationship sync methods
    # ==================================================================

    async def sync_relationships(self, db_session: AsyncSession) -> dict[str, int]:
        """Create all edges. Returns counts by type."""
        counts: dict[str, int] = {}
        counts["TARGETS"] = await self._sync_drug_target_edges(db_session)
        counts["PARTICIPATES_IN_target"] = await self._sync_target_pathway_edges(db_session)
        counts["PARTICIPATES_IN_gene"] = await self._sync_gene_pathway_edges(db_session)
        counts["gene_cancer"] = await self._sync_gene_cancer_edges(db_session)
        counts["INTERACTS_WITH"] = await self._sync_protein_interaction_edges(db_session)
        counts["MENTIONS_DRUG"] = await self._sync_literature_drug_edges(db_session)
        counts["STUDIES_CANCER"] = await self._sync_literature_cancer_edges(db_session)
        counts["MENTIONS_TARGET"] = await self._sync_literature_target_edges(db_session)
        counts["TESTS_DRUG"] = await self._sync_trial_drug_edges(db_session)
        counts["TARGETS_CANCER"] = await self._sync_trial_cancer_edges(db_session)
        counts["ASSOCIATED_WITH"] = await self._sync_target_disease_edges(db_session)
        logger.info("Relationship sync complete: %s", counts)
        return counts

    async def _sync_drug_target_edges(self, db_session: AsyncSession) -> int:
        """Drug -[TARGETS]-> Target"""
        result = await db_session.execute(text("""
            SELECT d.drugbank_id, t.uniprot_id, dt.action_type,
                   dt.binding_affinity_nm, dt.known_action, dt.source
            FROM drug_targets dt
            JOIN drugs d ON d.id = dt.drug_id
            JOIN targets t ON t.id = dt.target_id
            WHERE t.uniprot_id IS NOT NULL
        """))
        items = [
            {
                "drugbank_id": r[0], "uniprot_id": r[1],
                "action_type": r[2], "binding_affinity_nm": r[3],
                "known_action": r[4], "source": r[5],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (d:Drug {drugbank_id: edge.drugbank_id})
        MATCH (t:Target {uniprot_id: edge.uniprot_id})
        MERGE (d)-[r:TARGETS]->(t)
        SET r.action = edge.action_type,
            r.affinity_nm = edge.binding_affinity_nm,
            r.known_action = edge.known_action,
            r.source = edge.source
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d TARGETS edges", count)
        return count

    async def _sync_target_pathway_edges(self, db_session: AsyncSession) -> int:
        """Target -[PARTICIPATES_IN]-> Pathway"""
        result = await db_session.execute(text("""
            SELECT t.uniprot_id, p.external_id, pt.role
            FROM pathway_targets pt
            JOIN targets t ON t.id = pt.target_id
            JOIN pathways p ON p.id = pt.pathway_id
            WHERE t.uniprot_id IS NOT NULL
        """))
        items = [
            {"uniprot_id": r[0], "external_id": r[1], "role": r[2]}
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (t:Target {uniprot_id: edge.uniprot_id})
        MATCH (p:Pathway {external_id: edge.external_id})
        MERGE (t)-[r:PARTICIPATES_IN]->(p)
        SET r.role = edge.role
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Target-PARTICIPATES_IN-Pathway edges", count)
        return count

    async def _sync_gene_pathway_edges(self, db_session: AsyncSession) -> int:
        """Gene -[PARTICIPATES_IN]-> Pathway (from pathway.genes JSONB arrays)."""
        result = await db_session.execute(
            select(Pathway.external_id, Pathway.genes).where(
                Pathway.genes.isnot(None)
            )
        )
        items = []
        for external_id, genes_json in result.all():
            if isinstance(genes_json, list):
                for gene in genes_json:
                    if gene:
                        items.append({
                            "gene_symbol": str(gene),
                            "external_id": external_id,
                        })
        query = """
        UNWIND $batch AS edge
        MATCH (g:Gene {symbol: edge.gene_symbol})
        MATCH (p:Pathway {external_id: edge.external_id})
        MERGE (g)-[:PARTICIPATES_IN]->(p)
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d Gene-PARTICIPATES_IN-Pathway edges", count)
        return count

    async def _sync_gene_cancer_edges(self, db_session: AsyncSession) -> int:
        """Gene -[MUTATED_IN/OVEREXPRESSED_IN/etc]-> CancerType

        Only creates edges for frequency_percent > 3% to filter noise.
        Uses different edge types based on alteration_type.
        """
        # Map alteration types to Cypher relationship types
        alt_type_map = {
            "mutation": "MUTATED_IN",
            "overexpression": "OVEREXPRESSED_IN",
            "underexpression": "UNDEREXPRESSED_IN",
            "amplification": "AMPLIFIED_IN",
            "deletion": "DELETED_IN",
        }

        total = 0
        for alt_type, rel_type in alt_type_map.items():
            result = await db_session.execute(text(f"""
                SELECT cmp.gene_symbol, ct.tcga_code,
                       cmp.frequency_percent, cmp.expression_zscore
                FROM cancer_molecular_profiles cmp
                JOIN cancer_types ct ON ct.id = cmp.cancer_type_id
                WHERE cmp.alteration_type = :alt_type
                  AND (cmp.frequency_percent IS NULL OR cmp.frequency_percent > 3)
            """), {"alt_type": alt_type})

            items = [
                {
                    "gene_symbol": r[0], "tcga_code": r[1],
                    "frequency": r[2], "zscore": r[3],
                }
                for r in result.all()
            ]

            if not items:
                continue

            query = f"""
            UNWIND $batch AS edge
            MATCH (g:Gene {{symbol: edge.gene_symbol}})
            MATCH (c:CancerType {{tcga_code: edge.tcga_code}})
            MERGE (g)-[r:{rel_type}]->(c)
            SET r.frequency = edge.frequency,
                r.zscore = edge.zscore
            """
            count = await self._run_write_batched(query, items)
            logger.info("Synced %d %s edges", count, rel_type)
            total += count

        return total

    async def _sync_protein_interaction_edges(self, db_session: AsyncSession) -> int:
        """Target -[INTERACTS_WITH]-> Target"""
        result = await db_session.execute(text("""
            SELECT pi.protein_a_uniprot, pi.protein_b_uniprot,
                   pi.interaction_score, pi.experimental_score,
                   pi.database_score, pi.textmining_score
            FROM protein_interactions pi
            WHERE EXISTS (SELECT 1 FROM targets t WHERE t.uniprot_id = pi.protein_a_uniprot)
              AND EXISTS (SELECT 1 FROM targets t WHERE t.uniprot_id = pi.protein_b_uniprot)
        """))
        items = [
            {
                "protein_a": r[0], "protein_b": r[1],
                "score": r[2], "experimental": r[3],
                "database": r[4], "textmining": r[5],
            }
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (t1:Target {uniprot_id: edge.protein_a})
        MATCH (t2:Target {uniprot_id: edge.protein_b})
        MERGE (t1)-[r:INTERACTS_WITH]->(t2)
        SET r.score = edge.score,
            r.experimental = edge.experimental,
            r.database = edge.database,
            r.textmining = edge.textmining
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d INTERACTS_WITH edges", count)
        return count

    async def _sync_literature_drug_edges(self, db_session: AsyncSession) -> int:
        """Paper -[MENTIONS_DRUG]-> Drug"""
        result = await db_session.execute(text("""
            SELECT l.pmid, d.drugbank_id, ld.mention_type
            FROM literature_drugs ld
            JOIN literature l ON l.id = ld.literature_id
            JOIN drugs d ON d.id = ld.drug_id
        """))
        items = [
            {"pmid": r[0], "drugbank_id": r[1], "mention_type": r[2]}
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (p:Paper {pmid: edge.pmid})
        MATCH (d:Drug {drugbank_id: edge.drugbank_id})
        MERGE (p)-[r:MENTIONS_DRUG]->(d)
        SET r.mention_type = edge.mention_type
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d MENTIONS_DRUG edges", count)
        return count

    async def _sync_literature_cancer_edges(self, db_session: AsyncSession) -> int:
        """Paper -[STUDIES_CANCER]-> CancerType"""
        result = await db_session.execute(text("""
            SELECT l.pmid, ct.tcga_code, lc.mention_type
            FROM literature_cancers lc
            JOIN literature l ON l.id = lc.literature_id
            JOIN cancer_types ct ON ct.id = lc.cancer_type_id
        """))
        items = [
            {"pmid": r[0], "tcga_code": r[1], "mention_type": r[2]}
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (p:Paper {pmid: edge.pmid})
        MATCH (c:CancerType {tcga_code: edge.tcga_code})
        MERGE (p)-[r:STUDIES_CANCER]->(c)
        SET r.mention_type = edge.mention_type
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d STUDIES_CANCER edges", count)
        return count

    async def _sync_literature_target_edges(self, db_session: AsyncSession) -> int:
        """Paper -[MENTIONS_TARGET]-> Target"""
        result = await db_session.execute(text("""
            SELECT l.pmid, t.uniprot_id, lt.mention_type
            FROM literature_targets lt
            JOIN literature l ON l.id = lt.literature_id
            JOIN targets t ON t.id = lt.target_id
            WHERE t.uniprot_id IS NOT NULL
        """))
        items = [
            {"pmid": r[0], "uniprot_id": r[1], "mention_type": r[2]}
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (p:Paper {pmid: edge.pmid})
        MATCH (t:Target {uniprot_id: edge.uniprot_id})
        MERGE (p)-[r:MENTIONS_TARGET]->(t)
        SET r.mention_type = edge.mention_type
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d MENTIONS_TARGET edges", count)
        return count

    async def _sync_trial_drug_edges(self, db_session: AsyncSession) -> int:
        """ClinicalTrial -[TESTS_DRUG]-> Drug"""
        result = await db_session.execute(text("""
            SELECT ct.nct_id, d.drugbank_id
            FROM trial_drugs td
            JOIN clinical_trials ct ON ct.id = td.trial_id
            JOIN drugs d ON d.id = td.drug_id
        """))
        items = [
            {"nct_id": r[0], "drugbank_id": r[1]}
            for r in result.all()
        ]
        query = """
        UNWIND $batch AS edge
        MATCH (ct:ClinicalTrial {nct_id: edge.nct_id})
        MATCH (d:Drug {drugbank_id: edge.drugbank_id})
        MERGE (ct)-[:TESTS_DRUG]->(d)
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d TESTS_DRUG edges", count)
        return count

    async def _sync_trial_cancer_edges(self, db_session: AsyncSession) -> int:
        """ClinicalTrial -[TARGETS_CANCER]-> CancerType

        Maps trial conditions to TCGA cancer types.
        """
        # Build reverse map: tcga_code -> set of condition substrings
        tcga_to_conditions: dict[str, list[str]] = {}
        for condition, code in CONDITION_TO_TCGA.items():
            tcga_to_conditions.setdefault(code, []).append(condition)

        result = await db_session.execute(
            select(ClinicalTrial.nct_id, ClinicalTrial.conditions)
        )

        items = []
        seen = set()
        for nct_id, conditions_json in result.all():
            if not conditions_json or not isinstance(conditions_json, list):
                continue
            for condition in conditions_json:
                cond_lower = str(condition).lower().strip()
                matched_code = CONDITION_TO_TCGA.get(cond_lower)
                if matched_code and (nct_id, matched_code) not in seen:
                    items.append({"nct_id": nct_id, "tcga_code": matched_code})
                    seen.add((nct_id, matched_code))

        query = """
        UNWIND $batch AS edge
        MATCH (ct:ClinicalTrial {nct_id: edge.nct_id})
        MATCH (c:CancerType {tcga_code: edge.tcga_code})
        MERGE (ct)-[:TARGETS_CANCER]->(c)
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d TARGETS_CANCER edges", count)
        return count

    async def _sync_target_disease_edges(self, db_session: AsyncSession) -> int:
        """Target -[ASSOCIATED_WITH]-> CancerType (from OpenTargets)."""
        # Build a disease_name -> tcga_code lookup
        cancer_name_to_code: dict[str, str] = {}
        ct_result = await db_session.execute(
            select(CancerType.tcga_code, CancerType.name)
        )
        for code, name in ct_result.all():
            cancer_name_to_code[name.lower()] = code

        # Also include CONDITION_TO_TCGA for broader matching
        for cond, code in CONDITION_TO_TCGA.items():
            cancer_name_to_code[cond] = code

        result = await db_session.execute(text("""
            SELECT t.uniprot_id, tda.disease_name, tda.overall_score
            FROM target_disease_associations tda
            JOIN targets t ON t.id = tda.target_id
            WHERE t.uniprot_id IS NOT NULL
              AND tda.overall_score > 0.1
        """))

        items = []
        for uniprot_id, disease_name, score in result.all():
            if not disease_name:
                continue
            tcga_code = cancer_name_to_code.get(disease_name.lower())
            if tcga_code:
                items.append({
                    "uniprot_id": uniprot_id,
                    "tcga_code": tcga_code,
                    "score": score,
                })

        query = """
        UNWIND $batch AS edge
        MATCH (t:Target {uniprot_id: edge.uniprot_id})
        MATCH (c:CancerType {tcga_code: edge.tcga_code})
        MERGE (t)-[r:ASSOCIATED_WITH]->(c)
        SET r.score = edge.score,
            r.source = 'opentargets'
        """
        count = await self._run_write_batched(query, items)
        logger.info("Synced %d ASSOCIATED_WITH edges", count)
        return count

    # ==================================================================
    # Full sync orchestration
    # ==================================================================

    async def full_sync(self, db_session: AsyncSession) -> dict[str, Any]:
        """Full PostgreSQL -> Neo4j synchronization.

        Order: schema -> nodes -> edges.
        Returns summary of all synced counts.
        """
        logger.info("Starting full knowledge graph sync")
        summary: dict[str, Any] = {"nodes": {}, "edges": {}}

        # Phase 0: Schema
        await self.init_schema()

        # Phase 1: Nodes
        summary["nodes"]["Drug"] = await self.sync_drugs(db_session)
        summary["nodes"]["Target"] = await self.sync_targets(db_session)
        summary["nodes"]["Pathway"] = await self.sync_pathways(db_session)
        summary["nodes"]["CancerType"] = await self.sync_cancer_types(db_session)
        summary["nodes"]["Gene"] = await self.sync_genes(db_session)
        summary["nodes"]["Paper"] = await self.sync_papers(db_session)
        summary["nodes"]["ClinicalTrial"] = await self.sync_clinical_trials(db_session)
        summary["nodes"]["Mutation"] = await self.sync_mutations(db_session)

        # Phase 2: Edges
        summary["edges"] = await self.sync_relationships(db_session)

        total_nodes = sum(summary["nodes"].values())
        total_edges = sum(summary["edges"].values())
        summary["total_nodes"] = total_nodes
        summary["total_edges"] = total_edges

        logger.info(
            "Knowledge graph sync complete: %d nodes, %d edges",
            total_nodes, total_edges,
        )
        return summary
