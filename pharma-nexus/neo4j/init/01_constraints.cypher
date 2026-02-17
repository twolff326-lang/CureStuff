// Neo4j initialization — constraints and indexes for the Pharma Nexus knowledge graph
// All statements are idempotent (IF NOT EXISTS) — safe to run on every startup

// =====================================================
// Uniqueness constraints (also create indexes automatically)
// =====================================================

CREATE CONSTRAINT drug_drugbank_id IF NOT EXISTS FOR (d:Drug) REQUIRE d.drugbank_id IS UNIQUE;
CREATE CONSTRAINT target_uniprot_id IF NOT EXISTS FOR (t:Target) REQUIRE t.uniprot_id IS UNIQUE;
CREATE CONSTRAINT pathway_external_id IF NOT EXISTS FOR (p:Pathway) REQUIRE p.external_id IS UNIQUE;
CREATE CONSTRAINT cancer_type_tcga_code IF NOT EXISTS FOR (c:CancerType) REQUIRE c.tcga_code IS UNIQUE;
CREATE CONSTRAINT gene_symbol_unique IF NOT EXISTS FOR (g:Gene) REQUIRE g.symbol IS UNIQUE;
CREATE CONSTRAINT paper_pmid IF NOT EXISTS FOR (p:Paper) REQUIRE p.pmid IS UNIQUE;
CREATE CONSTRAINT clinical_trial_nct_id IF NOT EXISTS FOR (ct:ClinicalTrial) REQUIRE ct.nct_id IS UNIQUE;
CREATE CONSTRAINT mutation_pg_id IF NOT EXISTS FOR (m:Mutation) REQUIRE m.pg_id IS UNIQUE;

// =====================================================
// Additional indexes for common lookups
// =====================================================

CREATE INDEX drug_name_index IF NOT EXISTS FOR (d:Drug) ON (d.name);
CREATE INDEX target_gene_symbol_index IF NOT EXISTS FOR (t:Target) ON (t.gene_symbol);
CREATE INDEX gene_symbol_index IF NOT EXISTS FOR (g:Gene) ON (g.symbol);
CREATE INDEX cancer_type_name_index IF NOT EXISTS FOR (c:CancerType) ON (c.name);
CREATE INDEX pathway_name_index IF NOT EXISTS FOR (p:Pathway) ON (p.name);
CREATE INDEX paper_pub_year_index IF NOT EXISTS FOR (p:Paper) ON (p.pub_year);
CREATE INDEX trial_phase_index IF NOT EXISTS FOR (ct:ClinicalTrial) ON (ct.phase);
CREATE INDEX trial_status_index IF NOT EXISTS FOR (ct:ClinicalTrial) ON (ct.status);
