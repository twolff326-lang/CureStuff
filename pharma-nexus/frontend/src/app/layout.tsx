import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/common/Sidebar";
import { ToastProvider } from "@/components/common/Toast";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Pharma Nexus",
  description: "AI-powered drug repurposing discovery engine",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={inter.className}>
        <ToastProvider>
          <div className="flex h-screen">
            <Sidebar />
            <main className="flex-1 overflow-y-auto p-4 pt-16 md:p-8 md:pt-8">
              {children}
            </main>
          </div>
        </ToastProvider>
      </body>
    </html>
  );
}
