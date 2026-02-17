"""CLI runner for drug repurposing analysis.

Triggers analysis pipelines from the command line.

Usage:
    python scripts/run_analysis.py --cancer-type BRCA --min-score 50
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main():
    parser = argparse.ArgumentParser(
        description="Pharma Nexus - Drug Repurposing Analysis Runner"
    )
    parser.add_argument(
        "--cancer-type",
        type=str,
        help="TCGA cancer type code (e.g., BRCA, LUAD, GBM)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum composite score threshold (0-100)",
    )
    parser.add_argument(
        "--drug",
        type=str,
        help="Specific DrugBank ID to analyze",
    )

    args = parser.parse_args()

    print("Pharma Nexus - Analysis Runner")
    print("=" * 40)
    print(f"Cancer type: {args.cancer_type or 'all'}")
    print(f"Min score: {args.min_score}")
    print(f"Drug filter: {args.drug or 'none'}")
    print()
    print("Analysis pipeline will be implemented in future prompts.")


if __name__ == "__main__":
    main()
