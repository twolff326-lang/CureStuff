export default function DashboardPage() {
  return (
    <div>
      <h1 className="text-3xl font-bold text-slate-900 mb-6">Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Drugs Indexed" value="--" />
        <StatCard title="Hypotheses Generated" value="--" />
        <StatCard title="Cancer Types" value="--" />
        <StatCard title="Literature Articles" value="--" />
      </div>
      <div className="mt-8">
        <h2 className="text-xl font-semibold text-slate-800 mb-4">
          Top Hypotheses
        </h2>
        <p className="text-slate-500">
          No hypotheses generated yet. Ingest data sources to get started.
        </p>
      </div>
    </div>
  );
}

function StatCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
      <p className="text-sm font-medium text-slate-500">{title}</p>
      <p className="text-2xl font-bold text-slate-900 mt-1">{value}</p>
    </div>
  );
}
