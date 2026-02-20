"""ClinicalTrials.gov connector — clinical trial data for drug-cancer pairs.

Uses ClinicalTrials.gov API v2 to fetch active and completed trials
for drugs in cancer indications.

API base: https://clinicaltrials.gov/api/v2/studies
No API key required. Rate limit: ~2 req/sec.
"""

import logging
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, TrialDrug
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

CTGOV_API_BASE = "https://clinicaltrials.gov/api/v2/studies"

# Map common trial condition names to TCGA cancer type codes
CONDITION_TO_TCGA: dict[str, str] = {
    "breast cancer": "BRCA",
    "breast carcinoma": "BRCA",
    "breast neoplasm": "BRCA",
    "invasive breast cancer": "BRCA",
    "triple negative breast cancer": "BRCA",
    "her2 positive breast cancer": "BRCA",
    "non-small cell lung cancer": "LUAD",
    "non small cell lung cancer": "LUAD",
    "nsclc": "LUAD",
    "lung adenocarcinoma": "LUAD",
    "squamous cell lung cancer": "LUSC",
    "lung squamous cell carcinoma": "LUSC",
    "small cell lung cancer": "SCLC",
    "colorectal cancer": "COAD",
    "colon cancer": "COAD",
    "colon carcinoma": "COAD",
    "colon adenocarcinoma": "COAD",
    "rectal cancer": "READ",
    "rectal carcinoma": "READ",
    "pancreatic cancer": "PAAD",
    "pancreatic adenocarcinoma": "PAAD",
    "pancreatic carcinoma": "PAAD",
    "melanoma": "SKCM",
    "cutaneous melanoma": "SKCM",
    "skin melanoma": "SKCM",
    "glioblastoma": "GBM",
    "glioblastoma multiforme": "GBM",
    "low grade glioma": "LGG",
    "glioma": "LGG",
    "hepatocellular carcinoma": "LIHC",
    "liver cancer": "LIHC",
    "hepatic cancer": "LIHC",
    "renal cell carcinoma": "KIRC",
    "kidney cancer": "KIRC",
    "clear cell renal cell carcinoma": "KIRC",
    "kidney renal papillary cell carcinoma": "KIRP",
    "ovarian cancer": "OV",
    "ovarian carcinoma": "OV",
    "prostate cancer": "PRAD",
    "prostate adenocarcinoma": "PRAD",
    "bladder cancer": "BLCA",
    "urothelial carcinoma": "BLCA",
    "bladder carcinoma": "BLCA",
    "gastric cancer": "STAD",
    "stomach cancer": "STAD",
    "stomach adenocarcinoma": "STAD",
    "esophageal cancer": "ESCA",
    "esophageal carcinoma": "ESCA",
    "thyroid cancer": "THCA",
    "thyroid carcinoma": "THCA",
    "head and neck cancer": "HNSC",
    "head and neck squamous cell carcinoma": "HNSC",
    "cervical cancer": "CESC",
    "cervical carcinoma": "CESC",
    "endometrial cancer": "UCEC",
    "uterine cancer": "UCEC",
    "uterine carcinosarcoma": "UCS",
    "acute myeloid leukemia": "LAML",
    "aml": "LAML",
    "cholangiocarcinoma": "CHOL",
    "bile duct cancer": "CHOL",
    "adrenocortical carcinoma": "ACC",
    "pheochromocytoma": "PCPG",
    "paraganglioma": "PCPG",
    "mesothelioma": "MESO",
    "thymoma": "THYM",
    "uveal melanoma": "UVM",
    "testicular cancer": "TGCT",
    "diffuse large b-cell lymphoma": "DLBC",
    "sarcoma": "SARC",
    "soft tissue sarcoma": "SARC",
}


class ClinicalTrialsConnector(BaseConnector):
    """Fetches clinical trial data for drugs in cancer indications."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=2.0,
            batch_size=500,
            timeout=60.0,
            **kwargs,
        )
        self._headers = {"Accept": "application/json"}
        # Cache: tcga_code → cancer_type.id
        self._tcga_to_ct_id: dict[str, int] = {}

    def get_source_name(self) -> str:
        return "clinicaltrials"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch clinical trials for all drugs in cancer indications."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Build TCGA code → cancer_type.id cache
            result = await session.execute(
                select(CancerType.id, CancerType.tcga_code).where(
                    CancerType.tcga_code.isnot(None)
                )
            )
            for ct_id, code in result.all():
                if code:
                    self._tcga_to_ct_id[code] = ct_id

            # Get all drugs
            result = await session.execute(
                select(Drug.id, Drug.name).where(Drug.name.isnot(None))
            )
            drugs = result.all()
            logger.info(
                "Fetching clinical trials for %d drugs", len(drugs)
            )

            await self.set_total_expected(session, len(drugs))

            processed = 0
            for drug_id, drug_name in drugs:
                try:
                    count = await self._fetch_drug_trials(
                        client, session, drug_id, drug_name
                    )
                    processed += count

                    if processed % 500 == 0 and processed > 0:
                        await session.commit()
                        logger.info("Clinical trials progress: %d trials stored", processed)

                except Exception as exc:
                    self.record_error(
                        f"drug_{drug_name}", exc, record_id=str(drug_id)
                    )

            await session.commit()
            self._records_processed = processed
            logger.info("ClinicalTrials.gov: %d trials stored", processed)
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Per-drug trial fetch
    # ------------------------------------------------------------------

    async def _fetch_drug_trials(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        drug_id: int,
        drug_name: str,
    ) -> int:
        """Fetch cancer-related trials for a single drug."""
        cancer_terms = (
            "cancer OR tumor OR neoplasm OR carcinoma OR lymphoma "
            "OR leukemia OR melanoma OR sarcoma"
        )

        try:
            resp = await self.http_get(
                client,
                CTGOV_API_BASE,
                params={
                    "query.term": drug_name,
                    "query.cond": cancer_terms,
                    "filter.overallStatus": (
                        "COMPLETED,ACTIVE_NOT_RECRUITING,RECRUITING,"
                        "ENROLLING_BY_INVITATION"
                    ),
                    "pageSize": "50",
                },
                headers=self._headers,
            )
            data = resp.json()
        except Exception:
            return 0

        studies = data.get("studies", [])
        if not studies:
            return 0

        stored = 0
        for study in studies:
            try:
                record = self._parse_study(study)
                if not record:
                    continue

                # Match conditions to cancer_type_id via TCGA codes
                conditions = record.get("conditions", [])
                for condition in conditions:
                    tcga_code = CONDITION_TO_TCGA.get(condition.lower())
                    if tcga_code and tcga_code in self._tcga_to_ct_id:
                        record["cancer_type_id"] = self._tcga_to_ct_id[tcga_code]
                        break

                # Upsert trial
                stmt = pg_insert(ClinicalTrial.__table__).values(**record)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["nct_id"],
                    set_={
                        "title": stmt.excluded.title,
                        "status": stmt.excluded.status,
                        "phase": stmt.excluded.phase,
                        "cancer_type_id": stmt.excluded.cancer_type_id,
                        "conditions": stmt.excluded.conditions,
                        "interventions": stmt.excluded.interventions,
                        "enrollment": stmt.excluded.enrollment,
                        "start_date": stmt.excluded.start_date,
                        "completion_date": stmt.excluded.completion_date,
                        "results_summary": stmt.excluded.results_summary,
                        "source_url": stmt.excluded.source_url,
                    },
                )
                await session.execute(stmt)
                await session.flush()

                # Get trial DB ID
                trial_result = await session.execute(
                    select(ClinicalTrial.id).where(
                        ClinicalTrial.nct_id == record["nct_id"]
                    )
                )
                trial_db_id = trial_result.scalar_one()

                # Link trial to drug
                td_stmt = pg_insert(TrialDrug.__table__).values(
                    trial_id=trial_db_id, drug_id=drug_id
                )
                td_stmt = td_stmt.on_conflict_do_nothing(
                    index_elements=["trial_id", "drug_id"]
                )
                await session.execute(td_stmt)

                stored += 1

            except Exception as exc:
                nct = study.get("protocolSection", {}).get(
                    "identificationModule", {}
                ).get("nctId", "?")
                logger.debug("Failed to store trial %s: %s", nct, exc)

        return stored

    # ------------------------------------------------------------------
    # Study parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_study(study: dict) -> dict[str, Any] | None:
        """Parse a ClinicalTrials.gov API v2 study object."""
        try:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            conditions_mod = proto.get("conditionsModule", {})
            arms_mod = proto.get("armsInterventionsModule", {})

            nct_id = ident.get("nctId", "")
            if not nct_id:
                return None

            title = ident.get("officialTitle", "") or ident.get("briefTitle", "")
            overall_status = status_mod.get("overallStatus", "")

            # Phase — take highest if multiple
            phases = design.get("phases", [])
            phase = ""
            if phases:
                phase_priority = {
                    "PHASE4": 4, "PHASE3": 3, "PHASE2": 2,
                    "PHASE1": 1, "EARLY_PHASE1": 0, "NA": -1,
                }
                phases_sorted = sorted(
                    phases, key=lambda p: phase_priority.get(p, -1), reverse=True
                )
                phase = phases_sorted[0] if phases_sorted else ""

            # Conditions
            conditions = conditions_mod.get("conditions", [])

            # Interventions
            interventions_raw = arms_mod.get("interventions", [])
            interventions = []
            for intv in interventions_raw:
                interventions.append({
                    "type": intv.get("type", ""),
                    "name": intv.get("name", ""),
                    "description": intv.get("description", ""),
                })

            # Enrollment
            enrollment_info = design.get("enrollmentInfo", {})
            enrollment = enrollment_info.get("count")

            # Dates
            start_date = _parse_ctgov_date(
                status_mod.get("startDateStruct", {}).get("date", "")
            )
            completion_date = _parse_ctgov_date(
                status_mod.get("completionDateStruct", {}).get("date", "")
            )

            # Results summary
            results_section = study.get("resultsSection", {})
            results_summary = ""
            if results_section:
                # Try to get primary outcome results
                outcomes = results_section.get("outcomeMeasuresModule", {})
                measures = outcomes.get("outcomeMeasures", [])
                summaries = []
                for measure in measures[:3]:
                    measure_title = measure.get("title", "")
                    if measure_title:
                        summaries.append(measure_title)
                results_summary = "; ".join(summaries)

            source_url = f"https://clinicaltrials.gov/study/{nct_id}"

            return {
                "nct_id": nct_id,
                "title": title,
                "status": overall_status,
                "phase": phase,
                "conditions": conditions,
                "interventions": interventions,
                "enrollment": enrollment,
                "start_date": start_date,
                "completion_date": completion_date,
                "results_summary": results_summary or None,
                "source_url": source_url,
            }

        except Exception as exc:
            logger.debug("Failed to parse study: %s", exc)
            return None


def _parse_ctgov_date(date_str: str) -> date | None:
    """Parse ClinicalTrials.gov date string (YYYY-MM-DD or YYYY-MM or YYYY)."""
    if not date_str:
        return None
    try:
        parts = date_str.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None
