"""Quick script to check row counts across all major tables."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg2://pharma_nexus:change_me_in_production@postgres:5432/pharma_nexus",
)

engine = create_engine(DATABASE_URL)

TABLES = [
    "drugs", "targets", "drug_targets",
    "cancer_types", "molecular_profiles", "mutations",
    "pathways", "pathway_targets", "protein_interactions",
    "literature", "clinical_trials", "bioassays",
    "gene_dependencies", "combination_hypotheses",
    "repurposing_hypotheses", "pipeline_runs", "software_versions",
    "ingestion_log",
]

with engine.connect() as conn:
    total = 0
    print(f"\n{'Table':<30} {'Rows':>10}")
    print("-" * 42)
    for table in TABLES:
        try:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            total += count
            print(f"{table:<30} {count:>10,}")
        except Exception as e:
            print(f"{table:<30} {'ERROR':>10}  ({e.__class__.__name__})")
    print("-" * 42)
    print(f"{'TOTAL':<30} {total:>10,}")
    print()
