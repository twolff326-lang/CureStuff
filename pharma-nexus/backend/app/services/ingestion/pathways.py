"""Pathway seeding connector.

Seeds key cancer-related signaling pathways and their gene members.
Uses curated data from Reactome/KEGG for the most therapeutically
relevant pathways in oncology.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pathway import Pathway
from app.models.pathway_target import PathwayTarget
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

# Curated cancer-relevant pathways with key gene members
PATHWAY_DATA = [
    {
        "name": "PI3K-AKT-mTOR Signaling",
        "source_id": "R-HSA-2219528",
        "source": "reactome",
        "description": "Central growth/survival pathway frequently activated in cancer",
        "genes": ["PIK3CA", "PIK3CB", "PIK3R1", "AKT1", "AKT2", "MTOR", "PTEN",
                  "TSC1", "TSC2", "RICTOR", "RPTOR", "RPS6KB1", "EIF4E", "PDK1"],
    },
    {
        "name": "RAS-MAPK Signaling",
        "source_id": "R-HSA-5684996",
        "source": "reactome",
        "description": "Proliferation signaling through RAS-RAF-MEK-ERK cascade",
        "genes": ["KRAS", "NRAS", "HRAS", "BRAF", "RAF1", "MAP2K1", "MAP2K2",
                  "MAPK1", "MAPK3", "NF1", "SOS1", "GRB2", "ARAF"],
    },
    {
        "name": "TP53 Tumor Suppressor Pathway",
        "source_id": "R-HSA-3700989",
        "source": "reactome",
        "description": "Guardian of the genome — cell cycle arrest, apoptosis, senescence",
        "genes": ["TP53", "MDM2", "MDM4", "CDKN1A", "BAX", "BBC3", "PMAIP1",
                  "ATM", "ATR", "CHEK1", "CHEK2", "CDKN2A"],
    },
    {
        "name": "Cell Cycle Regulation",
        "source_id": "R-HSA-1640170",
        "source": "reactome",
        "description": "CDK-cyclin complexes controlling cell division",
        "genes": ["CDK4", "CDK6", "CDK2", "CDK1", "CCND1", "CCND2", "CCND3",
                  "CCNE1", "CCNE2", "RB1", "E2F1", "CDKN2A", "CDKN2B", "CDKN1B"],
    },
    {
        "name": "Apoptosis Signaling",
        "source_id": "R-HSA-109581",
        "source": "reactome",
        "description": "Programmed cell death — intrinsic and extrinsic pathways",
        "genes": ["BCL2", "BCL2L1", "MCL1", "BAX", "BAK1", "BID", "BAD",
                  "CASP3", "CASP8", "CASP9", "CYCS", "APAF1", "XIAP", "BIRC5"],
    },
    {
        "name": "WNT-Beta-Catenin Signaling",
        "source_id": "R-HSA-195721",
        "source": "reactome",
        "description": "Developmental pathway reactivated in cancer stem cells",
        "genes": ["CTNNB1", "APC", "AXIN1", "AXIN2", "GSK3B", "DVL1",
                  "TCF7L2", "LEF1", "RNF43", "ZNRF3", "LGR5"],
    },
    {
        "name": "Notch Signaling",
        "source_id": "R-HSA-157118",
        "source": "reactome",
        "description": "Cell fate determination and cancer stemness",
        "genes": ["NOTCH1", "NOTCH2", "NOTCH3", "FBXW7", "MAML1", "HES1",
                  "DLL1", "DLL4", "JAG1", "JAG2", "RBPJ"],
    },
    {
        "name": "DNA Damage Repair (Homologous Recombination)",
        "source_id": "R-HSA-5693532",
        "source": "reactome",
        "description": "BRCA1/2-dependent repair, target for PARP inhibitors",
        "genes": ["BRCA1", "BRCA2", "RAD51", "PALB2", "ATM", "ATR",
                  "CHEK1", "CHEK2", "PARP1", "PARP2", "BARD1", "RAD50", "MRE11"],
    },
    {
        "name": "Receptor Tyrosine Kinase Signaling",
        "source_id": "R-HSA-9006934",
        "source": "reactome",
        "description": "EGFR, HER2, MET, FGFR, PDGFR family signaling",
        "genes": ["EGFR", "ERBB2", "ERBB3", "MET", "FGFR1", "FGFR2", "FGFR3",
                  "PDGFRA", "PDGFRB", "KIT", "FLT3", "RET", "ALK", "ROS1", "NTRK1"],
    },
    {
        "name": "JAK-STAT Signaling",
        "source_id": "R-HSA-6785807",
        "source": "reactome",
        "description": "Cytokine signaling in hematologic malignancies",
        "genes": ["JAK1", "JAK2", "JAK3", "TYK2", "STAT3", "STAT5A", "STAT5B",
                  "SOCS1", "SOCS3", "SHP2", "CISH"],
    },
    {
        "name": "NF-kB Signaling",
        "source_id": "R-HSA-9020702",
        "source": "reactome",
        "description": "Inflammatory/survival signaling, key in lymphomas",
        "genes": ["NFKB1", "RELA", "IKBKB", "IKBKG", "TRAF2", "TRAF3",
                  "BIRC3", "MYD88", "CARD11", "BCL10", "MALT1", "TNFAIP3"],
    },
    {
        "name": "Chromatin Remodeling and Epigenetics",
        "source_id": "R-HSA-3247509",
        "source": "reactome",
        "description": "Epigenetic regulators frequently mutated in cancer",
        "genes": ["ARID1A", "ARID1B", "SMARCA4", "PBRM1", "SETD2", "KMT2C",
                  "KMT2D", "EZH2", "DNMT3A", "TET2", "IDH1", "IDH2", "KDM6A",
                  "NSD1", "CREBBP", "EP300"],
    },
    {
        "name": "Hippo Tumor Suppressor Pathway",
        "source_id": "R-HSA-2028269",
        "source": "reactome",
        "description": "Organ size control and contact inhibition",
        "genes": ["NF2", "LATS1", "LATS2", "YAP1", "WWTR1", "MST1", "MST2",
                  "SAV1", "MOB1A", "TEAD1"],
    },
    {
        "name": "Hedgehog Signaling",
        "source_id": "R-HSA-5358351",
        "source": "reactome",
        "description": "Developmental pathway in basal cell carcinoma and medulloblastoma",
        "genes": ["SMO", "PTCH1", "GLI1", "GLI2", "SUFU", "SHH", "IHH"],
    },
    {
        "name": "Angiogenesis (VEGF Signaling)",
        "source_id": "R-HSA-194138",
        "source": "reactome",
        "description": "Tumor blood vessel formation, anti-VEGF therapy target",
        "genes": ["VEGFA", "VEGFB", "VEGFC", "KDR", "FLT1", "FLT4",
                  "HIF1A", "EPAS1", "VHL", "ANGPT1", "ANGPT2", "TEK"],
    },
    {
        "name": "Immune Checkpoint Signaling",
        "source_id": "R-HSA-389948",
        "source": "reactome",
        "description": "PD-1/PD-L1 and CTLA-4 immune evasion pathways",
        "genes": ["PDCD1", "CD274", "CTLA4", "LAG3", "TIGIT", "HAVCR2",
                  "CD80", "CD86", "B2M", "JAK1", "JAK2", "IFNG", "CD8A"],
    },
    {
        "name": "Metabolic Reprogramming (Warburg Effect)",
        "source_id": "R-HSA-70171",
        "source": "reactome",
        "description": "Altered glucose metabolism and IDH mutations in cancer",
        "genes": ["HK2", "PKM", "LDHA", "SLC2A1", "IDH1", "IDH2",
                  "SDHA", "SDHB", "FH", "G6PD", "MYC"],
    },
    {
        "name": "TGF-Beta Signaling",
        "source_id": "R-HSA-170834",
        "source": "reactome",
        "description": "Dual role in tumor suppression and promotion",
        "genes": ["TGFBR1", "TGFBR2", "SMAD2", "SMAD3", "SMAD4",
                  "SMAD7", "ACVR1", "BMP2", "BMP4", "INHBA"],
    },
    {
        "name": "Protein Ubiquitination and Degradation",
        "source_id": "R-HSA-461",
        "source": "reactome",
        "description": "Proteasome pathway, target for bortezomib/carfilzomib",
        "genes": ["PSMA1", "PSMB5", "UBE2C", "FBXW7", "VHL", "KEAP1",
                  "CUL3", "RNF43", "SPOP", "SIAH1"],
    },
    {
        "name": "Splicing and RNA Processing",
        "source_id": "R-HSA-72163",
        "source": "reactome",
        "description": "Spliceosome mutations in hematologic malignancies",
        "genes": ["SF3B1", "U2AF1", "SRSF2", "ZRSR2", "DDX41",
                  "PRPF8", "SF3A1", "RBM10"],
    },
]


class PathwayConnector(BaseConnector):
    SOURCE_NAME = "pathways"

    async def run(self):
        await self._update_log(total_expected=len(PATHWAY_DATA))
        processed = 0

        for pw_info in PATHWAY_DATA:
            try:
                await self._seed_pathway(pw_info)
                processed += 1
                if processed % 5 == 0:
                    await self._update_log(records_processed=processed)
            except Exception as e:
                logger.warning(f"Failed to seed pathway {pw_info['name']}: {e}")
                continue

        await self._mark_completed(processed)
        logger.info(f"Pathway seed complete: {processed} pathways")

    async def _seed_pathway(self, info: dict):
        result = await self.db.execute(
            select(Pathway).where(Pathway.source_id == info["source_id"])
        )
        pathway = result.scalar_one_or_none()

        if not pathway:
            pathway = Pathway(
                name=info["name"],
                source_id=info["source_id"],
                source=info["source"],
                description=info["description"],
            )
            self.db.add(pathway)
            await self.db.commit()
            await self.db.refresh(pathway)

        # Seed pathway-gene memberships
        for gene_symbol in info.get("genes", []):
            result = await self.db.execute(
                select(PathwayTarget).where(
                    PathwayTarget.pathway_id == pathway.id,
                    PathwayTarget.gene_symbol == gene_symbol,
                )
            )
            if not result.scalar_one_or_none():
                pt = PathwayTarget(
                    pathway_id=pathway.id,
                    gene_symbol=gene_symbol,
                )
                self.db.add(pt)

        await self.db.commit()
