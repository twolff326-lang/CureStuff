"""Initial data seeding script for Pharma Nexus.

Loads foundational reference data (cancer types from TCGA, etc.)
into the database. Run after initial migration.

Usage:
    python scripts/seed_data.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main():
    print("Pharma Nexus - Data Seeding")
    print("=" * 40)
    print("Seed data loading will be implemented in future prompts.")
    print("This script will pre-load:")
    print("  - 33 TCGA cancer types")
    print("  - KEGG/Reactome pathway reference data")
    print("  - Common gene symbol mappings")


if __name__ == "__main__":
    main()
