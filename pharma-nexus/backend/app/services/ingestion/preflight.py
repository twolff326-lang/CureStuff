"""Preflight connectivity checker for all external data source APIs.

Runs a single lightweight probe against each API to verify:
  - Reachable (HTTP 200)
  - Returns real data (non-empty response)
  - Sample record count
  - Response time

Does NOT write to the database or modify any state.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 15.0  # seconds per probe


@dataclass
class ProbeResult:
    source: str
    display_name: str
    url: str
    reachable: bool = False
    has_data: bool = False
    response_time_ms: int = 0
    sample_count: int | None = None
    sample_preview: str = ""
    error: str = ""
    status_code: int | None = None
    notes: str = ""


async def _probe(
    client: httpx.AsyncClient,
    source: str,
    display_name: str,
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    headers: dict | None = None,
    json_body: dict | None = None,
    data_body: dict | None = None,
    extract_fn: Any = None,
    notes: str = "",
) -> ProbeResult:
    """Run a single probe request and return the result."""
    result = ProbeResult(source=source, display_name=display_name, url=url, notes=notes)
    t0 = time.monotonic()
    try:
        if method == "POST" and json_body:
            resp = await client.post(
                url, params=params, headers=headers, json=json_body,
                timeout=httpx.Timeout(PROBE_TIMEOUT),
            )
        elif method == "POST" and data_body:
            resp = await client.post(
                url, params=params, headers=headers, data=data_body,
                timeout=httpx.Timeout(PROBE_TIMEOUT),
            )
        else:
            resp = await client.get(
                url, params=params, headers=headers,
                timeout=httpx.Timeout(PROBE_TIMEOUT),
            )

        result.response_time_ms = int((time.monotonic() - t0) * 1000)
        result.status_code = resp.status_code

        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}"
            return result

        result.reachable = True

        # Use custom extractor if provided
        if extract_fn:
            count, preview = extract_fn(resp)
            result.sample_count = count
            result.sample_preview = preview
            result.has_data = count is not None and count > 0
        else:
            # Default: try JSON, check for non-empty
            try:
                data = resp.json()
                if isinstance(data, list):
                    result.sample_count = len(data)
                    result.has_data = len(data) > 0
                    if data:
                        result.sample_preview = _truncate(str(data[0]), 200)
                elif isinstance(data, dict):
                    result.has_data = True
                    result.sample_preview = _truncate(str(data), 200)
            except Exception:
                # Plain text response
                text = resp.text.strip()
                lines = text.split("\n")
                result.sample_count = len(lines)
                result.has_data = len(text) > 0
                result.sample_preview = _truncate(lines[0], 200) if lines else ""

    except httpx.TimeoutException:
        result.response_time_ms = int((time.monotonic() - t0) * 1000)
        result.error = f"Timeout after {PROBE_TIMEOUT}s"
    except httpx.ConnectError as exc:
        result.response_time_ms = int((time.monotonic() - t0) * 1000)
        result.error = f"Connection failed: {exc}"
    except Exception as exc:
        result.response_time_ms = int((time.monotonic() - t0) * 1000)
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def _truncate(s: str, max_len: int) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s


# ------------------------------------------------------------------
# Individual probe definitions
# ------------------------------------------------------------------

def _build_probes(client: httpx.AsyncClient) -> list:
    """Build the list of probe coroutines for all external APIs."""
    probes = []

    # 1. PubChem
    def _extract_pubchem(resp: httpx.Response):
        data = resp.json()
        props = data.get("PropertyTable", {}).get("Properties", [])
        if props:
            p = props[0]
            return 1, f"Aspirin: {p.get('MolecularFormula', '?')}, MW={p.get('MolecularWeight', '?')}"
        return 0, ""

    probes.append(_probe(
        client, "pubchem", "PubChem",
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/Aspirin/property/MolecularFormula,MolecularWeight,CanonicalSMILES/JSON",
        extract_fn=_extract_pubchem,
        notes="Free, no auth required",
    ))

    # 2. ChEMBL
    def _extract_chembl(resp: httpx.Response):
        data = resp.json()
        mechs = data.get("mechanisms", [])
        total = data.get("page_meta", {}).get("total_count", len(mechs))
        preview = ""
        if mechs:
            m = mechs[0]
            preview = f"{m.get('molecule_chembl_id', '?')}: {m.get('mechanism_of_action', '?')}"
        return total, preview

    probes.append(_probe(
        client, "chembl", "ChEMBL",
        "https://www.ebi.ac.uk/chembl/api/data/mechanism.json",
        params={"max_phase": "4", "limit": "1"},
        extract_fn=_extract_chembl,
        notes="Free, no auth required",
    ))

    # 3. cBioPortal
    def _extract_cbioportal(resp: httpx.Response):
        studies = resp.json()
        tcga = [s for s in studies if s.get("studyId", "").endswith("_tcga")]
        preview = ""
        if tcga:
            s = tcga[0]
            preview = f"{s.get('studyId')}: {s.get('name', '?')} ({s.get('allSampleCount', '?')} samples)"
        return len(tcga), preview

    probes.append(_probe(
        client, "cbioportal", "cBioPortal",
        "https://www.cbioportal.org/api/studies",
        params={"projection": "SUMMARY", "pageSize": "1000"},
        headers={"Accept": "application/json"},
        extract_fn=_extract_cbioportal,
        notes="Free, no auth required",
    ))

    # 4. TCGA / GDC
    import json as _json

    def _extract_gdc(resp: httpx.Response):
        data = resp.json()
        hits = data.get("data", {}).get("hits", [])
        total = data.get("data", {}).get("pagination", {}).get("total", len(hits))
        preview = ""
        if hits:
            h = hits[0]
            preview = f"{h.get('project_id', '?')}: {h.get('name', '?')}"
        return total, preview

    gdc_filters = _json.dumps({"op": "eq", "content": {"field": "program.name", "value": "TCGA"}})
    probes.append(_probe(
        client, "tcga_gdc", "TCGA / GDC",
        "https://api.gdc.cancer.gov/projects",
        params={"filters": gdc_filters, "size": "3", "fields": "project_id,name,primary_site"},
        headers={"Accept": "application/json"},
        extract_fn=_extract_gdc,
        notes="Free, no auth required",
    ))

    # 5. KEGG
    def _extract_kegg(resp: httpx.Response):
        lines = [l for l in resp.text.strip().split("\n") if l.strip()]
        preview = ""
        if lines:
            parts = lines[0].split("\t", 1)
            preview = parts[1].strip() if len(parts) == 2 else lines[0][:100]
        return len(lines), preview

    probes.append(_probe(
        client, "kegg", "KEGG",
        "https://rest.kegg.jp/list/pathway/hsa",
        extract_fn=_extract_kegg,
        notes="Free, 1 req/sec limit (academic)",
    ))

    # 6. Reactome
    def _extract_reactome(resp: httpx.Response):
        pathways = resp.json()
        preview = ""
        if pathways and isinstance(pathways, list):
            p = pathways[0]
            preview = f"{p.get('stId', '?')}: {p.get('displayName', '?')}"
        count = len(pathways) if isinstance(pathways, list) else 0
        return count, preview

    probes.append(_probe(
        client, "reactome", "Reactome",
        "https://reactome.org/ContentService/data/pathways/top/9606",
        headers={"Accept": "application/json"},
        extract_fn=_extract_reactome,
        notes="Free, no auth required",
    ))

    # 7. STRING
    def _extract_string(resp: httpx.Response):
        data = resp.json()
        if isinstance(data, list) and data:
            # Network endpoint returns list of interactions
            return len(data), f"Interactions found: {len(data)}"
        return 0, ""

    probes.append(_probe(
        client, "string", "STRING",
        "https://string-db.org/api/json/network",
        method="POST",
        data_body={
            "identifiers": "EGFR",
            "species": "9606",
            "required_score": "400",
            "limit": "5",
            "caller_identity": "pharma_nexus_preflight",
        },
        extract_fn=_extract_string,
        notes="Free, no auth required",
    ))

    # 8. UniProt
    def _extract_uniprot(resp: httpx.Response):
        data = resp.json()
        results = data.get("results", [])
        preview = ""
        if results:
            r = results[0]
            acc = r.get("primaryAccession", "?")
            genes = r.get("genes", [{}])
            symbol = genes[0].get("geneName", {}).get("value", "?") if genes else "?"
            preview = f"{acc} ({symbol})"
        return len(results), preview

    probes.append(_probe(
        client, "uniprot", "UniProt",
        "https://rest.uniprot.org/uniprotkb/search",
        params={"query": "accession:P04637", "fields": "accession,gene_names", "format": "json", "size": "1"},
        headers={"Accept": "application/json"},
        extract_fn=_extract_uniprot,
        notes="Free, no auth required. P04637 = TP53",
    ))

    # 9. OpenTargets
    def _extract_opentargets(resp: httpx.Response):
        data = resp.json()
        target = data.get("data", {}).get("target")
        if not target:
            return 0, ""
        assoc = target.get("associatedDiseases", {})
        count = assoc.get("count", 0)
        rows = assoc.get("rows", [])
        preview = ""
        if rows:
            d = rows[0].get("disease", {})
            preview = f"Top: {d.get('name', '?')} (score={rows[0].get('score', '?')})"
        return count, preview

    ot_query = """query { target(ensemblId: "ENSG00000146648") {
        approvedSymbol associatedDiseases(page: { index: 0, size: 3 }) {
            count rows { disease { name } score }
        }
    }}"""
    probes.append(_probe(
        client, "opentargets", "OpenTargets",
        "https://api.platform.opentargets.org/api/v4/graphql",
        method="POST",
        json_body={"query": ot_query},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        extract_fn=_extract_opentargets,
        notes="Free, no auth required. Probing EGFR associations",
    ))

    # 10. PubMed / NCBI
    def _extract_pubmed(resp: httpx.Response):
        data = resp.json()
        result = data.get("esearchresult", {})
        count = int(result.get("count", 0))
        ids = result.get("idlist", [])
        preview = f"Total matching: {count:,}"
        if ids:
            preview += f", sample PMIDs: {', '.join(ids[:3])}"
        return count, preview

    pubmed_params = {
        "db": "pubmed",
        "term": "cancer drug repurposing",
        "retmax": "3",
        "retmode": "json",
    }
    if settings.ncbi_api_key:
        pubmed_params["api_key"] = settings.ncbi_api_key
    probes.append(_probe(
        client, "pubmed", "PubMed / NCBI",
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=pubmed_params,
        extract_fn=_extract_pubmed,
        notes=f"{'API key configured' if settings.ncbi_api_key else 'No API key (3 req/sec limit)'}",
    ))

    # 11. ClinicalTrials.gov
    def _extract_ctgov(resp: httpx.Response):
        data = resp.json()
        studies = data.get("studies", [])
        total = data.get("totalCount", len(studies))
        preview = ""
        if studies:
            proto = studies[0].get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            nct = ident.get("nctId", "?")
            title = ident.get("briefTitle", "?")
            preview = f"{nct}: {_truncate(title, 100)}"
        return total, preview

    probes.append(_probe(
        client, "clinicaltrials", "ClinicalTrials.gov",
        "https://clinicaltrials.gov/api/v2/studies",
        params={
            "query.cond": "cancer",
            "query.term": "drug repurposing",
            "pageSize": "1",
        },
        headers={"Accept": "application/json"},
        extract_fn=_extract_ctgov,
        notes="Free, no auth required",
    ))

    return probes


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

async def run_preflight_checks() -> list[dict[str, Any]]:
    """Run all API preflight probes concurrently and return results.

    Returns a list of dicts, each representing one probe result.
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        limits=httpx.Limits(max_connections=15, max_keepalive_connections=10),
    ) as client:
        probes = _build_probes(client)
        results: list[ProbeResult] = await asyncio.gather(*probes, return_exceptions=False)

    output = []
    for r in results:
        output.append({
            "source": r.source,
            "display_name": r.display_name,
            "url": r.url,
            "reachable": r.reachable,
            "has_data": r.has_data,
            "response_time_ms": r.response_time_ms,
            "sample_count": r.sample_count,
            "sample_preview": r.sample_preview,
            "error": r.error,
            "status_code": r.status_code,
            "notes": r.notes,
        })

    return output
