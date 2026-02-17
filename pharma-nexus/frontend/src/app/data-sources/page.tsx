export default function DataSourcesPage() {
  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-6">Data Sources</h1>
      <p className="text-slate-500 mb-6">
        Manage data ingestion from public biomedical databases.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {sources.map((source) => (
          <div
            key={source.name}
            className="bg-white rounded-lg shadow-sm border border-slate-200 p-5"
          >
            <h3 className="font-semibold text-slate-800">{source.name}</h3>
            <p className="text-sm text-slate-500 mt-1">{source.description}</p>
            <span className="inline-block mt-3 text-xs font-medium px-2 py-1 rounded-full bg-slate-100 text-slate-600">
              Not ingested
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const sources = [
  { name: "DrugBank", description: "FDA-approved drug data, mechanisms, targets" },
  { name: "PubChem", description: "Chemical properties, bioassay results" },
  { name: "ChEMBL", description: "Bioactivity data, binding affinities" },
  { name: "TCGA", description: "Molecular profiles of 11,000+ tumors" },
  { name: "COSMIC", description: "Somatic mutations in cancer" },
  { name: "KEGG", description: "Biological pathway maps" },
  { name: "Reactome", description: "Pathway database with molecular details" },
  { name: "UniProt", description: "Protein function and interaction data" },
  { name: "PubMed", description: "Published biomedical literature" },
  { name: "ClinicalTrials.gov", description: "Active and completed trial data" },
  { name: "GEO", description: "Gene expression datasets" },
  { name: "STRING", description: "Protein-protein interaction networks" },
  { name: "OpenTargets", description: "Target-disease association evidence" },
];
