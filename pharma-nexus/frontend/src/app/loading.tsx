export default function Loading() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* Header skeleton */}
      <div className="flex items-center justify-between">
        <div>
          <div className="h-8 w-48 bg-slate-200 dark:bg-slate-700 rounded" />
          <div className="h-4 w-64 bg-slate-100 dark:bg-slate-800 rounded mt-2" />
        </div>
        <div className="h-10 w-32 bg-slate-200 dark:bg-slate-700 rounded-lg" />
      </div>

      {/* Stat cards skeleton */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <div
            key={i}
            className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-5"
          >
            <div className="h-3 w-20 bg-slate-100 dark:bg-slate-700 rounded mb-3" />
            <div className="h-8 w-16 bg-slate-200 dark:bg-slate-600 rounded" />
          </div>
        ))}
      </div>

      {/* Table skeleton */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700">
        <div className="p-4 border-b border-slate-200 dark:border-slate-700">
          <div className="h-5 w-40 bg-slate-200 dark:bg-slate-600 rounded" />
        </div>
        {[...Array(6)].map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-4 px-4 py-3 border-b border-slate-100 dark:border-slate-700/50"
          >
            <div className="h-4 w-8 bg-slate-100 dark:bg-slate-700 rounded" />
            <div className="h-4 w-48 bg-slate-100 dark:bg-slate-700 rounded" />
            <div className="h-4 w-24 bg-slate-100 dark:bg-slate-700 rounded flex-1" />
            <div className="h-6 w-16 bg-slate-200 dark:bg-slate-600 rounded-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
