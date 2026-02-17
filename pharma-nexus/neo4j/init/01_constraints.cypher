// Neo4j initialization - create constraints and indexes for the knowledge graph

// Uniqueness constraints
CREATE CONSTRAINT drug_drugbank_id IF NOT EXISTS FOR (d:Drug) REQUIRE d.drugbank_id IS UNIQUE;
CREATE CONSTRAINT target_uniprot_id IF NOT EXISTS FOR (t:Target) REQUIRE t.uniprot_id IS UNIQUE;
CREATE CONSTRAINT pathway_external_id IF NOT EXISTS FOR (p:Pathway) REQUIRE p.external_id IS UNIQUE;
CREATE CONSTRAINT cancer_type_tcga_code IF NOT EXISTS FOR (c:CancerType) REQUIRE c.tcga_code IS UNIQUE;
CREATE CONSTRAINT literature_pmid IF NOT EXISTS FOR (l:Literature) REQUIRE l.pmid IS UNIQUE;
CREATE CONSTRAINT clinical_trial_nct_id IF NOT EXISTS FOR (ct:ClinicalTrial) REQUIRE ct.nct_id IS UNIQUE;

// Indexes for common lookups
CREATE INDEX drug_name_index IF NOT EXISTS FOR (d:Drug) ON (d.name);
CREATE INDEX target_gene_symbol_index IF NOT EXISTS FOR (t:Target) ON (t.gene_symbol);
CREATE INDEX pathway_name_index IF NOT EXISTS FOR (p:Pathway) ON (p.name);
CREATE INDEX cancer_type_name_index IF NOT EXISTS FOR (c:CancerType) ON (c.name);
