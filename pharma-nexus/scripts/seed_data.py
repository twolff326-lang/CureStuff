"""Initial data seeding and ingestion CLI for Pharma Nexus.

Kicks off data ingestion from biomedical sources. Can run via Celery
tasks or directly (synchronous mode).

Usage:
    python scripts/seed_data.py --source all_drugs
    python scripts/seed_data.py --source drugbank --xml-path /data/drugbank.xml
    python scripts/seed_data.py --source pubchem
    python scripts/seed_data.py --source chembl
    python scripts/seed_data.py --source cbioportal
    python scripts/seed_data.py --source tcga
    python scripts/seed_data.py --source cosmic --census-tsv-path /data/cancer_gene_census.tsv
    python scripts/seed_data.py --source all_cancer_data
    python scripts/seed_data.py --source drugbank --sync
"""

import argparse
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_data")

VALID_SOURCES = {
    "drugbank", "pubchem", "chembl", "all_drugs",
    "cbioportal", "tcga", "cosmic", "all_cancer_data",
}


async def run_sync(
    source: str,
    xml_path: str | None = None,
    census_tsv_path: str | None = None,
):
    """Run connector directly without Celery (for development/testing)."""
    from app.database import async_session_factory

    connector_map = {
        "drugbank": ("app.services.ingestion.drugbank", "DrugBankConnector"),
        "pubchem": ("app.services.ingestion.pubchem", "PubChemConnector"),
        "chembl": ("app.services.ingestion.chembl", "ChEMBLConnector"),
        "cbioportal": ("app.services.ingestion.cbioportal", "CBioPortalConnector"),
        "tcga": ("app.services.ingestion.tcga", "TCGAConnector"),
        "cosmic": ("app.services.ingestion.cosmic", "COSMICConnector"),
    }

    if source == "all_drugs":
        for s in ["drugbank", "pubchem", "chembl"]:
            await run_sync(s, xml_path=xml_path if s == "drugbank" else None)
        return

    if source == "all_cancer_data":
        # cBioPortal first (creates cancer types), then TCGA + COSMIC
        await run_sync("cbioportal")
        await run_sync("tcga")
        await run_sync("cosmic", census_tsv_path=census_tsv_path)
        return

    if source not in connector_map:
        logger.error("Unknown source: %s", source)
        return

    module_path, class_name = connector_map[source]
    import importlib
    module = importlib.import_module(module_path)
    connector_class = getattr(module, class_name)

    async with async_session_factory() as session:
        kwargs = {"db_session": session}
        if source == "drugbank" and xml_path:
            kwargs["xml_path"] = xml_path
        if source == "cosmic" and census_tsv_path:
            kwargs["census_tsv_path"] = census_tsv_path

        connector = connector_class(**kwargs)
        logger.info("Running %s connector (sync mode)...", source)
        result = await connector.run()
        logger.info(
            "Done: %d records processed, %d errors",
            result["records_processed"],
            result["errors_count"],
        )
        if result["errors"]:
            for err in result["errors"][:10]:
                logger.warning("  Error: %s", err)


def run_via_celery(
    source: str,
    xml_path: str | None = None,
    census_tsv_path: str | None = None,
):
    """Dispatch ingestion via Celery task queue."""
    from app.tasks.celery_app import celery_app

    task_map = {
        "drugbank": "app.tasks.ingest.ingest_drugbank",
        "pubchem": "app.tasks.ingest.ingest_pubchem",
        "chembl": "app.tasks.ingest.ingest_chembl",
        "all_drugs": "app.tasks.ingest.ingest_all_drugs",
        "cbioportal": "app.tasks.ingest.ingest_cbioportal",
        "tcga": "app.tasks.ingest.ingest_tcga",
        "cosmic": "app.tasks.ingest.ingest_cosmic",
        "all_cancer_data": "app.tasks.ingest.ingest_all_cancer_data",
    }

    task_name = task_map[source]
    kwargs = {}
    if source in ("drugbank", "all_drugs") and xml_path:
        kwargs["xml_path"] = xml_path
    if source in ("cosmic", "all_cancer_data") and census_tsv_path:
        kwargs["census_tsv_path"] = census_tsv_path

    result = celery_app.send_task(task_name, kwargs=kwargs)
    logger.info("Task dispatched: %s (task_id=%s)", task_name, result.id)
    logger.info("Monitor via: GET /api/ingestion/status/%s", result.id)


def main():
    parser = argparse.ArgumentParser(
        description="Pharma Nexus - Data Ingestion CLI"
    )
    parser.add_argument(
        "--source",
        type=str,
        required=True,
        choices=sorted(VALID_SOURCES),
        help="Data source to ingest",
    )
    parser.add_argument(
        "--xml-path",
        type=str,
        default=None,
        help="Path to DrugBank XML file (for drugbank/all_drugs sources)",
    )
    parser.add_argument(
        "--census-tsv-path",
        type=str,
        default=None,
        help="Path to COSMIC Cancer Gene Census TSV file (for cosmic/all_cancer_data)",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Run directly without Celery (synchronous mode)",
    )

    args = parser.parse_args()

    print("Pharma Nexus - Data Ingestion")
    print("=" * 40)
    print(f"Source: {args.source}")
    print(f"Mode: {'sync (direct)' if args.sync else 'async (Celery)'}")
    if args.xml_path:
        print(f"XML path: {args.xml_path}")
    if args.census_tsv_path:
        print(f"Census TSV path: {args.census_tsv_path}")
    print()

    if args.sync:
        asyncio.run(
            run_sync(
                args.source,
                xml_path=args.xml_path,
                census_tsv_path=args.census_tsv_path,
            )
        )
    else:
        run_via_celery(
            args.source,
            xml_path=args.xml_path,
            census_tsv_path=args.census_tsv_path,
        )


if __name__ == "__main__":
    main()
