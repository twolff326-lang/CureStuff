import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/common/Sidebar";

export const metadata: Metadata = {
  title: "Pharma Nexus",
  description: "Drug repurposing discovery platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="font-sans">
        <div className="flex h-screen">
          <Sidebar />
          <main className="flex-1 overflow-y-auto bg-slate-50 p-8">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
