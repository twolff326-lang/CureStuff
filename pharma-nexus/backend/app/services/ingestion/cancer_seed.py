"""Cancer type and mutation seeding connector.

Seeds the database with ~30 cancer types and their most frequently mutated
genes. Uses curated data from TCGA/COSMIC literature rather than live API calls.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerType
from app.models.mutation import Mutation
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

# Curated cancer types with TCGA codes, tissues, and top mutated genes
# Frequencies are approximate from TCGA Pan-Cancer Atlas
CANCER_DATA = [
    {
        "name": "Breast Cancer",
        "tcga_code": "BRCA",
        "tissue": "Breast",
        "description": "Invasive breast carcinoma, most common cancer in women",
        "mutations": [
            ("PIK3CA", "missense", 0.36), ("TP53", "missense", 0.33),
            ("CDH1", "truncating", 0.11), ("GATA3", "frameshift", 0.10),
            ("MAP3K1", "truncating", 0.08), ("KMT2C", "truncating", 0.07),
            ("ESR1", "missense", 0.05), ("ERBB2", "amplification", 0.20),
            ("MYC", "amplification", 0.15), ("CCND1", "amplification", 0.15),
        ],
    },
    {
        "name": "Non-Small Cell Lung Cancer",
        "tcga_code": "LUAD",
        "tissue": "Lung",
        "description": "Lung adenocarcinoma, leading cause of cancer death",
        "mutations": [
            ("TP53", "missense", 0.46), ("KRAS", "missense", 0.33),
            ("EGFR", "missense", 0.14), ("STK11", "truncating", 0.17),
            ("KEAP1", "truncating", 0.13), ("NF1", "truncating", 0.11),
            ("BRAF", "missense", 0.07), ("PIK3CA", "missense", 0.05),
            ("ALK", "fusion", 0.05), ("ROS1", "fusion", 0.02),
        ],
    },
    {
        "name": "Colorectal Cancer",
        "tcga_code": "COAD",
        "tissue": "Colon",
        "description": "Colorectal adenocarcinoma, third most common cancer",
        "mutations": [
            ("APC", "truncating", 0.75), ("TP53", "missense", 0.59),
            ("KRAS", "missense", 0.43), ("PIK3CA", "missense", 0.18),
            ("SMAD4", "truncating", 0.15), ("FBXW7", "missense", 0.11),
            ("BRAF", "missense", 0.10), ("NRAS", "missense", 0.05),
            ("ERBB2", "amplification", 0.05), ("MET", "amplification", 0.03),
        ],
    },
    {
        "name": "Melanoma",
        "tcga_code": "SKCM",
        "tissue": "Skin",
        "description": "Cutaneous melanoma, most aggressive skin cancer",
        "mutations": [
            ("BRAF", "missense", 0.52), ("NRAS", "missense", 0.28),
            ("TP53", "missense", 0.19), ("CDKN2A", "deletion", 0.41),
            ("NF1", "truncating", 0.14), ("PTEN", "truncating", 0.12),
            ("KIT", "missense", 0.04), ("MAP2K1", "missense", 0.06),
        ],
    },
    {
        "name": "Pancreatic Cancer",
        "tcga_code": "PAAD",
        "tissue": "Pancreas",
        "description": "Pancreatic ductal adenocarcinoma, highly lethal",
        "mutations": [
            ("KRAS", "missense", 0.93), ("TP53", "missense", 0.72),
            ("CDKN2A", "deletion", 0.30), ("SMAD4", "truncating", 0.25),
            ("BRCA2", "truncating", 0.05), ("ARID1A", "truncating", 0.06),
        ],
    },
    {
        "name": "Ovarian Cancer",
        "tcga_code": "OV",
        "tissue": "Ovary",
        "description": "High-grade serous ovarian carcinoma",
        "mutations": [
            ("TP53", "missense", 0.96), ("BRCA1", "truncating", 0.12),
            ("BRCA2", "truncating", 0.11), ("NF1", "truncating", 0.04),
            ("RB1", "truncating", 0.02), ("CDK12", "truncating", 0.03),
            ("CCNE1", "amplification", 0.20), ("MYC", "amplification", 0.30),
        ],
    },
    {
        "name": "Prostate Cancer",
        "tcga_code": "PRAD",
        "tissue": "Prostate",
        "description": "Prostate adenocarcinoma, most common cancer in men",
        "mutations": [
            ("SPOP", "missense", 0.11), ("TP53", "missense", 0.08),
            ("FOXA1", "missense", 0.04), ("PTEN", "deletion", 0.17),
            ("AR", "amplification", 0.02), ("BRCA2", "truncating", 0.03),
            ("ATM", "truncating", 0.04), ("CDK12", "truncating", 0.02),
            ("TMPRSS2-ERG", "fusion", 0.46),
        ],
    },
    {
        "name": "Glioblastoma",
        "tcga_code": "GBM",
        "tissue": "Brain",
        "description": "Glioblastoma multiforme, most aggressive brain tumor",
        "mutations": [
            ("TP53", "missense", 0.28), ("PTEN", "truncating", 0.24),
            ("EGFR", "amplification", 0.57), ("CDKN2A", "deletion", 0.58),
            ("NF1", "truncating", 0.10), ("PIK3CA", "missense", 0.06),
            ("IDH1", "missense", 0.05), ("PIK3R1", "truncating", 0.08),
            ("RB1", "truncating", 0.08), ("PDGFRA", "amplification", 0.10),
        ],
    },
    {
        "name": "Chronic Myeloid Leukemia",
        "tcga_code": "LAML",
        "tissue": "Blood",
        "description": "CML driven by BCR-ABL1 fusion, paradigm for targeted therapy",
        "mutations": [
            ("BCR-ABL1", "fusion", 0.95), ("ABL1", "missense", 0.10),
            ("TP53", "missense", 0.03), ("RUNX1", "truncating", 0.05),
            ("ASXL1", "truncating", 0.05),
        ],
    },
    {
        "name": "Acute Myeloid Leukemia",
        "tcga_code": "AML",
        "tissue": "Blood",
        "description": "Aggressive blood cancer with diverse genetic subtypes",
        "mutations": [
            ("FLT3", "missense", 0.28), ("NPM1", "frameshift", 0.27),
            ("DNMT3A", "missense", 0.26), ("IDH2", "missense", 0.12),
            ("IDH1", "missense", 0.08), ("TET2", "truncating", 0.08),
            ("RUNX1", "truncating", 0.10), ("TP53", "missense", 0.08),
            ("NRAS", "missense", 0.10), ("CEBPA", "truncating", 0.06),
        ],
    },
    {
        "name": "Chronic Lymphocytic Leukemia",
        "tcga_code": "CLL",
        "tissue": "Blood",
        "description": "Most common leukemia in adults, B-cell malignancy",
        "mutations": [
            ("TP53", "missense", 0.10), ("ATM", "truncating", 0.09),
            ("SF3B1", "missense", 0.10), ("NOTCH1", "truncating", 0.10),
            ("MYD88", "missense", 0.03), ("BIRC3", "truncating", 0.04),
            ("BTK", "missense", 0.02),
        ],
    },
    {
        "name": "Renal Cell Carcinoma",
        "tcga_code": "KIRC",
        "tissue": "Kidney",
        "description": "Clear cell renal cell carcinoma, most common kidney cancer",
        "mutations": [
            ("VHL", "truncating", 0.52), ("PBRM1", "truncating", 0.33),
            ("SETD2", "truncating", 0.12), ("BAP1", "truncating", 0.10),
            ("KDM5C", "truncating", 0.07), ("MTOR", "missense", 0.06),
            ("TP53", "missense", 0.02), ("PIK3CA", "missense", 0.03),
        ],
    },
    {
        "name": "Hepatocellular Carcinoma",
        "tcga_code": "LIHC",
        "tissue": "Liver",
        "description": "Primary liver cancer, often linked to hepatitis/cirrhosis",
        "mutations": [
            ("TP53", "missense", 0.31), ("CTNNB1", "missense", 0.27),
            ("AXIN1", "truncating", 0.08), ("ARID1A", "truncating", 0.07),
            ("ARID2", "truncating", 0.05), ("TERT", "promoter", 0.60),
        ],
    },
    {
        "name": "Gastric Cancer",
        "tcga_code": "STAD",
        "tissue": "Stomach",
        "description": "Gastric adenocarcinoma with diverse molecular subtypes",
        "mutations": [
            ("TP53", "missense", 0.48), ("CDH1", "truncating", 0.11),
            ("PIK3CA", "missense", 0.10), ("ARID1A", "truncating", 0.08),
            ("KRAS", "missense", 0.06), ("ERBB2", "amplification", 0.12),
            ("RHOA", "missense", 0.05), ("MET", "amplification", 0.05),
        ],
    },
    {
        "name": "Bladder Cancer",
        "tcga_code": "BLCA",
        "tissue": "Bladder",
        "description": "Urothelial bladder carcinoma",
        "mutations": [
            ("TP53", "missense", 0.49), ("KDM6A", "truncating", 0.26),
            ("FGFR3", "missense", 0.14), ("PIK3CA", "missense", 0.15),
            ("RB1", "truncating", 0.13), ("ARID1A", "truncating", 0.13),
            ("CDKN2A", "deletion", 0.30), ("ERBB2", "amplification", 0.09),
        ],
    },
    {
        "name": "Head and Neck Squamous Cell Carcinoma",
        "tcga_code": "HNSC",
        "tissue": "Head and Neck",
        "description": "Squamous cell carcinoma of oral cavity, pharynx, larynx",
        "mutations": [
            ("TP53", "missense", 0.72), ("CDKN2A", "deletion", 0.22),
            ("PIK3CA", "missense", 0.17), ("NOTCH1", "truncating", 0.15),
            ("HRAS", "missense", 0.05), ("CASP8", "truncating", 0.08),
            ("FAT1", "truncating", 0.12), ("NSD1", "truncating", 0.10),
        ],
    },
    {
        "name": "Endometrial Cancer",
        "tcga_code": "UCEC",
        "tissue": "Uterus",
        "description": "Endometrial carcinoma, most common gynecologic malignancy",
        "mutations": [
            ("PTEN", "truncating", 0.52), ("PIK3CA", "missense", 0.48),
            ("TP53", "missense", 0.28), ("ARID1A", "truncating", 0.34),
            ("CTNNB1", "missense", 0.19), ("PIK3R1", "truncating", 0.17),
            ("KRAS", "missense", 0.16), ("FGFR2", "missense", 0.10),
        ],
    },
    {
        "name": "Thyroid Cancer",
        "tcga_code": "THCA",
        "tissue": "Thyroid",
        "description": "Thyroid carcinoma, mostly differentiated papillary type",
        "mutations": [
            ("BRAF", "missense", 0.60), ("NRAS", "missense", 0.08),
            ("HRAS", "missense", 0.04), ("RET", "fusion", 0.07),
            ("EIF1AX", "missense", 0.02), ("TERT", "promoter", 0.10),
        ],
    },
    {
        "name": "Esophageal Cancer",
        "tcga_code": "ESCA",
        "tissue": "Esophagus",
        "description": "Esophageal adenocarcinoma and squamous cell carcinoma",
        "mutations": [
            ("TP53", "missense", 0.72), ("CDKN2A", "deletion", 0.15),
            ("ERBB2", "amplification", 0.13), ("PIK3CA", "missense", 0.06),
            ("NFE2L2", "missense", 0.05), ("NOTCH1", "truncating", 0.08),
        ],
    },
    {
        "name": "Cervical Cancer",
        "tcga_code": "CESC",
        "tissue": "Cervix",
        "description": "Cervical squamous cell carcinoma, HPV-driven",
        "mutations": [
            ("PIK3CA", "missense", 0.26), ("EP300", "truncating", 0.08),
            ("FBXW7", "missense", 0.07), ("PTEN", "truncating", 0.06),
            ("TP53", "missense", 0.05), ("KRAS", "missense", 0.04),
            ("STK11", "truncating", 0.02), ("ERBB2", "amplification", 0.03),
        ],
    },
    {
        "name": "Sarcoma",
        "tcga_code": "SARC",
        "tissue": "Soft Tissue",
        "description": "Diverse group of mesenchymal tumors",
        "mutations": [
            ("TP53", "missense", 0.31), ("RB1", "truncating", 0.10),
            ("ATRX", "truncating", 0.10), ("CDKN2A", "deletion", 0.08),
            ("NF1", "truncating", 0.05), ("MDM2", "amplification", 0.15),
        ],
    },
    {
        "name": "Multiple Myeloma",
        "tcga_code": "MM",
        "tissue": "Bone Marrow",
        "description": "Plasma cell neoplasm in bone marrow",
        "mutations": [
            ("KRAS", "missense", 0.23), ("NRAS", "missense", 0.20),
            ("TP53", "missense", 0.08), ("BRAF", "missense", 0.04),
            ("FAM46C", "truncating", 0.11), ("DIS3", "missense", 0.10),
            ("TRAF3", "truncating", 0.04), ("CCND1", "amplification", 0.15),
        ],
    },
    {
        "name": "Diffuse Large B-Cell Lymphoma",
        "tcga_code": "DLBC",
        "tissue": "Lymph Node",
        "description": "Most common aggressive non-Hodgkin lymphoma",
        "mutations": [
            ("MYD88", "missense", 0.29), ("CD79B", "missense", 0.18),
            ("TP53", "missense", 0.21), ("KMT2D", "truncating", 0.24),
            ("CREBBP", "truncating", 0.12), ("EZH2", "missense", 0.22),
            ("BCL2", "translocation", 0.30), ("BCL6", "translocation", 0.25),
        ],
    },
    {
        "name": "Cholangiocarcinoma",
        "tcga_code": "CHOL",
        "tissue": "Bile Duct",
        "description": "Bile duct cancer, rare but aggressive",
        "mutations": [
            ("TP53", "missense", 0.27), ("KRAS", "missense", 0.17),
            ("IDH1", "missense", 0.13), ("IDH2", "missense", 0.05),
            ("ARID1A", "truncating", 0.07), ("BAP1", "truncating", 0.10),
            ("FGFR2", "fusion", 0.14), ("PBRM1", "truncating", 0.06),
        ],
    },
    {
        "name": "Mesothelioma",
        "tcga_code": "MESO",
        "tissue": "Pleura",
        "description": "Pleural mesothelioma, asbestos-related",
        "mutations": [
            ("BAP1", "truncating", 0.57), ("NF2", "truncating", 0.38),
            ("TP53", "missense", 0.16), ("CDKN2A", "deletion", 0.72),
            ("SETD2", "truncating", 0.08), ("LATS2", "truncating", 0.05),
        ],
    },
    {
        "name": "Testicular Cancer",
        "tcga_code": "TGCT",
        "tissue": "Testis",
        "description": "Testicular germ cell tumors, highly curable",
        "mutations": [
            ("KIT", "missense", 0.18), ("KRAS", "missense", 0.10),
            ("TP53", "missense", 0.02), ("BRAF", "missense", 0.02),
            ("NRAS", "missense", 0.02),
        ],
    },
    {
        "name": "Adrenocortical Carcinoma",
        "tcga_code": "ACC",
        "tissue": "Adrenal Gland",
        "description": "Rare adrenal cortex malignancy",
        "mutations": [
            ("TP53", "missense", 0.16), ("CTNNB1", "missense", 0.16),
            ("ZNRF3", "truncating", 0.21), ("PRKAR1A", "truncating", 0.08),
            ("MEN1", "truncating", 0.07), ("CDKN2A", "deletion", 0.15),
        ],
    },
    {
        "name": "Gastrointestinal Stromal Tumor",
        "tcga_code": "GIST",
        "tissue": "GI Tract",
        "description": "GIST, paradigm for KIT-targeted therapy",
        "mutations": [
            ("KIT", "missense", 0.80), ("PDGFRA", "missense", 0.10),
            ("SDHA", "truncating", 0.02), ("NF1", "truncating", 0.02),
            ("BRAF", "missense", 0.01),
        ],
    },
    {
        "name": "Small Cell Lung Cancer",
        "tcga_code": "SCLC",
        "tissue": "Lung",
        "description": "Aggressive neuroendocrine lung cancer",
        "mutations": [
            ("TP53", "missense", 0.90), ("RB1", "truncating", 0.90),
            ("NOTCH1", "truncating", 0.10), ("CREBBP", "truncating", 0.06),
            ("KMT2D", "truncating", 0.05), ("PTEN", "truncating", 0.05),
            ("MYC", "amplification", 0.16),
        ],
    },
]


class CancerSeedConnector(BaseConnector):
    SOURCE_NAME = "cancer_seed"

    async def run(self):
        await self._update_log(total_expected=len(CANCER_DATA))
        processed = 0

        for cancer_info in CANCER_DATA:
            try:
                await self._seed_cancer_type(cancer_info)
                processed += 1
                if processed % 5 == 0:
                    await self._update_log(records_processed=processed)
            except Exception as e:
                logger.warning(f"Failed to seed {cancer_info['name']}: {e}")
                continue

        await self._mark_completed(processed)
        logger.info(f"Cancer seed complete: {processed} cancer types with mutations")

    async def _seed_cancer_type(self, info: dict):
        result = await self.db.execute(
            select(CancerType).where(CancerType.tcga_code == info["tcga_code"])
        )
        cancer_type = result.scalar_one_or_none()

        if not cancer_type:
            cancer_type = CancerType(
                name=info["name"],
                tcga_code=info["tcga_code"],
                tissue=info["tissue"],
                description=info["description"],
            )
            self.db.add(cancer_type)
            await self.db.commit()
            await self.db.refresh(cancer_type)
        else:
            cancer_type.description = info["description"]
            cancer_type.tissue = info["tissue"]
            await self.db.commit()

        # Seed mutations
        for gene_symbol, mutation_type, frequency in info.get("mutations", []):
            result = await self.db.execute(
                select(Mutation).where(
                    Mutation.cancer_type_id == cancer_type.id,
                    Mutation.gene_symbol == gene_symbol,
                )
            )
            existing = result.scalar_one_or_none()
            if not existing:
                mutation = Mutation(
                    cancer_type_id=cancer_type.id,
                    gene_symbol=gene_symbol,
                    mutation_type=mutation_type,
                    frequency=frequency,
                )
                self.db.add(mutation)

        await self.db.commit()
