"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { name: "Dashboard", href: "/" },
  { name: "Hypotheses", href: "/hypotheses" },
  { name: "Discovery", href: "/discovery" },
  { name: "Reports", href: "/reports" },
  { name: "Knowledge Graph", href: "/knowledge-graph" },
  { name: "Analysis", href: "/analysis" },
  { name: "Data Sources", href: "/data-sources" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 bg-slate-900 text-white flex flex-col">
      <div className="p-6">
        <h1 className="text-xl font-bold">Pharma Nexus</h1>
        <p className="text-xs text-slate-400 mt-1">
          Drug Repurposing Engine
        </p>
      </div>
      <nav className="flex-1 px-3">
        {navigation.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.name}
              href={item.href}
              className={`block px-3 py-2 rounded-md text-sm font-medium mb-1 ${
                isActive
                  ? "bg-slate-800 text-white"
                  : "text-slate-300 hover:bg-slate-800 hover:text-white"
              }`}
            >
              {item.name}
            </Link>
          );
        })}
      </nav>
      <div className="p-4 border-t border-slate-800">
        <p className="text-xs text-slate-500">v0.1.0</p>
      </div>
    </aside>
  );
}
