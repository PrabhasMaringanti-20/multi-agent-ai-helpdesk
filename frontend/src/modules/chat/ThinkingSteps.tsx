import { useEffect, useState } from "react";

// Mirrors the real agent pipeline (which maps to the L1/L2 support flow):
// triage the issue (L1) → search the resolution knowledge base (L2) →
// draft a grounded answer → verify it's grounded.
const STEPS = [
  "Understanding the issue",
  "Searching the resolution knowledge base",
  "Drafting a grounded answer",
  "Checking the answer is grounded",
];

export function ThinkingSteps() {
  const [active, setActive] = useState(0);

  useEffect(() => {
    const t = setInterval(
      () => setActive((a) => Math.min(a + 1, STEPS.length - 1)),
      1100,
    );
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-w-[250px]">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-brand-500" />
        AI is working…
      </div>
      <ul className="space-y-1">
        {STEPS.map((step, i) => {
          const done = i < active;
          const current = i === active;
          return (
            <li key={step} className="flex items-center gap-2 text-xs">
              <span
                className={
                  done
                    ? "text-emerald-600"
                    : current
                      ? "text-brand-600"
                      : "text-slate-300"
                }
              >
                {done ? "✓" : current ? "◐" : "○"}
              </span>
              <span
                className={
                  current
                    ? "font-medium text-slate-800"
                    : done
                      ? "text-slate-500"
                      : "text-slate-400"
                }
              >
                {step}
                {current && <span className="animate-pulse">…</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
