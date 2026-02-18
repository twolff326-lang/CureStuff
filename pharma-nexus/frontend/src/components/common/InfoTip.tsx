"use client";

import { useState, useRef, useEffect } from "react";

/**
 * Inline info tooltip — a small (?) icon that shows a plain-English
 * explanation on hover/click. Used throughout the app so anyone off
 * the street can understand what a figure represents.
 */
export default function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex items-center ml-1">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        aria-label="More info"
        className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-slate-200 text-slate-500 hover:bg-slate-300 hover:text-slate-700 text-[10px] font-bold leading-none cursor-help transition-colors"
      >
        ?
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 px-3 py-2 text-xs leading-relaxed text-slate-700 bg-white border border-slate-200 rounded-lg shadow-lg"
        >
          {text}
          <span className="absolute top-full left-1/2 -translate-x-1/2 -mt-px w-2 h-2 bg-white border-b border-r border-slate-200 rotate-45" />
        </span>
      )}
    </span>
  );
}
