"""Pre-built Cypher queries for hypothesis discovery and graph exploration.

Provides parameterized graph traversal methods used by the hypothesis engine
(Prompt 8) and the knowledge graph API endpoints.

Key query patterns:
  - Direct targeting: drugs whose targets are altered in a cancer
  - Pathway-mediated: drugs sharing a pathway with cancer-altered genes
  - Interaction-mediated: drugs whose targets interact with cancer proteins
  - Multi-hop path finder: all paths between drug and cancer (1-4 hops)
  - Literature/trial connections: evidence-backed drug-cancer links
  - Subgraph extraction: D3.js-ready node/edge structures for visualization
"""

import logging
from typing import Any

from neo4j import AsyncDriver

logger = logging.getLogger(__name__)


class GraphQueryService:
    """Pre-built graph queries for hypothesis discovery and exploration."""

    def __init__(self, driver: AsyncDriver):
        self.driver = driver

    async def _read(
        self, query: str, parameters: dict | None = None, timeout: float = 30
    ) -> list[dict[str, Any]]:
        """Execute a read query and return list of record dicts."""
        async with self.driver.session() as session:
            result = await session.run(query, parameters or {})
            return [record.data() async for record in result]

    # ==================================================================
    # Direct targeting
    # ==================================================================

    async def find_direct_targets(self, cancer_tcga_code: str) -> list[dict]:
        """Find drugs that directly target genes altered in this cancer.

        Pattern: Drug -[TARGETS]-> Target <-[IS_TARGET]- Gene -[MUTATED_IN|...]-> CancerType
        """
        query = """
        MATCH (d:Drug)-[dt:TARGETS]->(t:Target)<-[:IS_TARGET]-(g:Gene)-[alt:MUTATED_IN|OVEREXPRESSED_IN|AMPLIFIED_IN]->(c:CancerType {tcga_code: $cancer})
        RETURN d.drugbank_id AS drug_id, d.name AS drug_name,
               d.mechanism_of_action AS mechanism,
               t.uniprot_id AS target_id, t.gene_symbol AS gene,
               t.protein_class AS protein_class,
               dt.action AS action_type, dt.affinity_nm AS affinity,
               type(alt) AS alteration_type, alt.frequency AS frequency
        ORDER BY alt.frequency DESC, dt.affinity_nm ASC
        """
        return await self._read(query, {"cancer": cancer_tcga_code})

    # ==================================================================
    # Pathway-mediated discovery
    # ==================================================================

    async def find_pathway_connections(self, cancer_tcga_code: str) -> list[dict]:
        """Find drugs whose targets share a PATHWAY with cancer-altered genes.

        This is where NOVEL repurposing discoveries happen.
        Pattern: Drug -> Target -> Pathway <- Gene -> CancerType
        """
        query = """
        MATCH (d:Drug)-[dt:TARGETS]->(t:Target)-[:PARTICIPATES_IN]->(p:Pathway)<-[:PARTICIPATES_IN]-(g:Gene)-[alt:MUTATED_IN|OVEREXPRESSED_IN|AMPLIFIED_IN]->(c:CancerType {tcga_code: $cancer})
        WHERE t.gene_symbol <> g.symbol
        RETURN d.drugbank_id AS drug_id, d.name AS drug_name,
               t.gene_symbol AS drug_target, t.uniprot_id AS target_id,
               p.name AS pathway_name, p.external_id AS pathway_id,
               p.source AS pathway_source,
               g.symbol AS cancer_gene,
               type(alt) AS alteration_type, alt.frequency AS frequency,
               dt.action AS action_type, dt.affinity_nm AS affinity
        ORDER BY alt.frequency DESC
        LIMIT 500
        """
        return await self._read(query, {"cancer": cancer_tcga_code})

    # ==================================================================
    # Interaction-mediated discovery
    # ==================================================================

    async def find_interaction_connections(self, cancer_tcga_code: str) -> list[dict]:
        """Find drugs whose targets physically INTERACT with cancer-relevant proteins.

        Pattern: Drug -> Target1 -[INTERACTS_WITH]-> Target2 <- Gene -> CancerType
        Only includes interactions with score >= 700 (high confidence).
        """
        query = """
        MATCH (d:Drug)-[dt:TARGETS]->(t1:Target)-[int:INTERACTS_WITH]->(t2:Target)<-[:IS_TARGET]-(g:Gene)-[alt:MUTATED_IN|OVEREXPRESSED_IN|AMPLIFIED_IN]->(c:CancerType {tcga_code: $cancer})
        WHERE int.score >= 700
        RETURN d.drugbank_id AS drug_id, d.name AS drug_name,
               t1.gene_symbol AS drug_target,
               t2.gene_symbol AS interacting_protein,
               int.score AS interaction_score,
               int.experimental AS experimental_score,
               g.symbol AS cancer_gene,
               type(alt) AS alteration_type, alt.frequency AS frequency
        ORDER BY int.score DESC, alt.frequency DESC
        LIMIT 300
        """
        return await self._read(query, {"cancer": cancer_tcga_code})

    # ==================================================================
    # Multi-hop path finder
    # ==================================================================

    async def find_all_paths(
        self, drug_drugbank_id: str, cancer_tcga_code: str, max_hops: int = 3
    ) -> list[dict]:
        """Find ALL paths between a specific drug and cancer type.

        Traverses through targets, pathways, interactions, and genes.
        max_hops is sanitized to 1-4 to prevent expensive queries.
        """
        safe_max_hops = min(max(int(max_hops), 1), 4)
        query = f"""
        MATCH path = (d:Drug {{drugbank_id: $drug_id}})-[*1..{safe_max_hops}]-(c:CancerType {{tcga_code: $cancer}})
        WHERE ALL(r IN relationships(path) WHERE type(r) IN [
            'TARGETS', 'PARTICIPATES_IN', 'IS_TARGET',
            'MUTATED_IN', 'OVEREXPRESSED_IN', 'AMPLIFIED_IN', 'DELETED_IN',
            'INTERACTS_WITH', 'ASSOCIATED_WITH'
        ])
        RETURN [n IN nodes(path) | {{
            labels: labels(n),
            name: COALESCE(n.name, n.gene_symbol, n.symbol, n.drugbank_id, n.tcga_code),
            id: COALESCE(n.drugbank_id, n.uniprot_id, n.external_id, n.tcga_code, n.symbol)
        }}] AS node_sequence,
        [r IN relationships(path) | {{
            type: type(r),
            properties: properties(r)
        }}] AS edge_sequence,
        length(path) AS hops
        ORDER BY hops ASC
        LIMIT 50
        """
        return await self._read(
            query,
            {"drug_id": drug_drugbank_id, "cancer": cancer_tcga_code},
            timeout=30,
        )

    # ==================================================================
    # Literature connections
    # ==================================================================

    async def find_literature_connections(
        self, cancer_tcga_code: str | None = None
    ) -> list[dict]:
        """Find drug-cancer pairs with published literature evidence."""
        where_clause = "WHERE c.tcga_code = $cancer" if cancer_tcga_code else ""
        query = f"""
        MATCH (d:Drug)<-[:MENTIONS_DRUG]-(p:Paper)-[:STUDIES_CANCER]->(c:CancerType)
        {where_clause}
        WITH d, c, count(p) AS paper_count, collect(p.pmid)[0..5] AS sample_pmids
        WHERE paper_count >= 2
        RETURN d.drugbank_id AS drug_id, d.name AS drug_name,
               c.tcga_code AS cancer_code, c.name AS cancer_name,
               paper_count, sample_pmids
        ORDER BY paper_count DESC
        LIMIT 100
        """
        params = {"cancer": cancer_tcga_code} if cancer_tcga_code else {}
        return await self._read(query, params)

    # ==================================================================
    # Clinical trial connections
    # ==================================================================

    async def find_trial_connections(
        self, cancer_tcga_code: str | None = None
    ) -> list[dict]:
        """Find drug-cancer pairs being tested in clinical trials."""
        where_clause = "WHERE c.tcga_code = $cancer" if cancer_tcga_code else ""
        query = f"""
        MATCH (d:Drug)<-[:TESTS_DRUG]-(ct:ClinicalTrial)-[:TARGETS_CANCER]->(c:CancerType)
        {where_clause}
        RETURN d.drugbank_id AS drug_id, d.name AS drug_name,
               c.tcga_code AS cancer_code, c.name AS cancer_name,
               ct.nct_id AS trial_id, ct.title AS trial_title,
               ct.phase AS phase, ct.status AS status
        ORDER BY ct.phase DESC, ct.status
        """
        params = {"cancer": cancer_tcga_code} if cancer_tcga_code else {}
        return await self._read(query, params)

    # ==================================================================
    # Subgraph extraction (for D3.js visualization)
    # ==================================================================

    async def get_drug_neighborhood(
        self, drug_drugbank_id: str, depth: int = 2
    ) -> dict[str, list]:
        """Get the subgraph around a drug for visualization.

        Returns {"nodes": [...], "edges": [...]} for D3.js force-directed graph.
        """
        safe_depth = min(max(int(depth), 1), 3)
        query = f"""
        MATCH path = (d:Drug {{drugbank_id: $drug_id}})-[*1..{safe_depth}]-(connected)
        WHERE connected:Target OR connected:Pathway OR connected:CancerType OR connected:Gene
        UNWIND nodes(path) AS n
        UNWIND relationships(path) AS r
        WITH collect(DISTINCT n) AS all_nodes, collect(DISTINCT r) AS all_rels
        RETURN [n IN all_nodes | {{
            id: COALESCE(n.drugbank_id, n.uniprot_id, n.external_id, n.tcga_code, n.symbol, toString(id(n))),
            type: labels(n)[0],
            label: COALESCE(n.name, n.gene_symbol, n.symbol, n.drugbank_id, n.tcga_code),
            pg_id: n.pg_id
        }}] AS nodes,
        [r IN all_rels | {{
            source: COALESCE(startNode(r).drugbank_id, startNode(r).uniprot_id, startNode(r).external_id, startNode(r).tcga_code, startNode(r).symbol, toString(id(startNode(r)))),
            target: COALESCE(endNode(r).drugbank_id, endNode(r).uniprot_id, endNode(r).external_id, endNode(r).tcga_code, endNode(r).symbol, toString(id(endNode(r)))),
            type: type(r),
            properties: properties(r)
        }}] AS edges
        """
        records = await self._read(query, {"drug_id": drug_drugbank_id})
        if records:
            return {"nodes": records[0].get("nodes", []), "edges": records[0].get("edges", [])}
        return {"nodes": [], "edges": []}

    async def get_cancer_neighborhood(
        self, cancer_tcga_code: str, depth: int = 1
    ) -> dict[str, list]:
        """Get the subgraph around a cancer type."""
        safe_depth = min(max(int(depth), 1), 3)
        query = f"""
        MATCH path = (c:CancerType {{tcga_code: $cancer}})-[*1..{safe_depth}]-(connected)
        WHERE connected:Gene OR connected:Target OR connected:Pathway OR connected:Drug
        WITH nodes(path) AS ns, relationships(path) AS rs
        UNWIND ns AS n
        UNWIND rs AS r
        WITH collect(DISTINCT n) AS all_nodes, collect(DISTINCT r) AS all_rels
        RETURN [n IN all_nodes | {{
            id: COALESCE(n.drugbank_id, n.uniprot_id, n.external_id, n.tcga_code, n.symbol, toString(id(n))),
            type: labels(n)[0],
            label: COALESCE(n.name, n.gene_symbol, n.symbol, n.drugbank_id, n.tcga_code),
            pg_id: n.pg_id
        }}] AS nodes,
        [r IN all_rels | {{
            source: COALESCE(startNode(r).drugbank_id, startNode(r).uniprot_id, startNode(r).external_id, startNode(r).tcga_code, startNode(r).symbol, toString(id(startNode(r)))),
            target: COALESCE(endNode(r).drugbank_id, endNode(r).uniprot_id, endNode(r).external_id, endNode(r).tcga_code, endNode(r).symbol, toString(id(endNode(r)))),
            type: type(r),
            properties: properties(r)
        }}] AS edges
        """
        records = await self._read(query, {"cancer": cancer_tcga_code})
        if records:
            return {"nodes": records[0].get("nodes", []), "edges": records[0].get("edges", [])}
        return {"nodes": [], "edges": []}

    async def get_hypothesis_subgraph(
        self, drug_drugbank_id: str, cancer_tcga_code: str
    ) -> dict[str, list]:
        """Get the subgraph connecting a specific drug to a cancer.

        Shows all paths up to 3 hops between the drug and cancer,
        highlighting the structural connections.
        """
        query = """
        MATCH path = (d:Drug {drugbank_id: $drug_id})-[*1..3]-(c:CancerType {tcga_code: $cancer})
        WHERE ALL(r IN relationships(path) WHERE type(r) IN [
            'TARGETS', 'PARTICIPATES_IN', 'IS_TARGET',
            'MUTATED_IN', 'OVEREXPRESSED_IN', 'AMPLIFIED_IN', 'DELETED_IN',
            'INTERACTS_WITH', 'ASSOCIATED_WITH'
        ])
        WITH nodes(path) AS ns, relationships(path) AS rs
        UNWIND ns AS n
        UNWIND rs AS r
        WITH collect(DISTINCT n) AS all_nodes, collect(DISTINCT r) AS all_rels
        RETURN [n IN all_nodes | {
            id: COALESCE(n.drugbank_id, n.uniprot_id, n.external_id, n.tcga_code, n.symbol, toString(id(n))),
            type: labels(n)[0],
            label: COALESCE(n.name, n.gene_symbol, n.symbol, n.drugbank_id, n.tcga_code),
            pg_id: n.pg_id
        }] AS nodes,
        [r IN all_rels | {
            source: COALESCE(startNode(r).drugbank_id, startNode(r).uniprot_id, startNode(r).external_id, startNode(r).tcga_code, startNode(r).symbol, toString(id(startNode(r)))),
            target: COALESCE(endNode(r).drugbank_id, endNode(r).uniprot_id, endNode(r).external_id, endNode(r).tcga_code, endNode(r).symbol, toString(id(endNode(r)))),
            type: type(r),
            properties: properties(r)
        }] AS edges
        """
        records = await self._read(
            query, {"drug_id": drug_drugbank_id, "cancer": cancer_tcga_code}
        )
        if records:
            return {"nodes": records[0].get("nodes", []), "edges": records[0].get("edges", [])}
        return {"nodes": [], "edges": []}

    # ==================================================================
    # Graph statistics
    # ==================================================================

    async def get_graph_stats(self) -> dict[str, Any]:
        """Get summary statistics about the knowledge graph."""
        # Node counts by label
        node_query = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS cnt
        ORDER BY cnt DESC
        """
        node_records = await self._read(node_query)
        node_counts = {r["label"]: r["cnt"] for r in node_records}

        # Edge counts by type
        edge_query = """
        MATCH ()-[r]->()
        RETURN type(r) AS rel_type, count(r) AS cnt
        ORDER BY cnt DESC
        """
        edge_records = await self._read(edge_query)
        edge_counts = {r["rel_type"]: r["cnt"] for r in edge_records}

        return {
            "nodes": node_counts,
            "edges": edge_counts,
            "total_nodes": sum(node_counts.values()),
            "total_edges": sum(edge_counts.values()),
        }
