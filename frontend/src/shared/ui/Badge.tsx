import type { ReactNode } from "react";

type Tone = "gray" | "green" | "amber" | "red" | "indigo";

const TONES: Record<Tone, string> = {
  gray: "bg-slate-100 text-slate-700",
  green: "bg-green-100 text-green-700",
  amber: "bg-amber-100 text-amber-700",
  red: "bg-red-100 text-red-700",
  indigo: "bg-brand-100 text-brand-700",
};

const STATUS_TONE: Record<string, Tone> = {
  open: "amber",
  triaged: "indigo",
  in_progress: "indigo",
  awaiting_user: "amber",
  resolved: "green",
  closed: "gray",
  reopened: "red",
  urgent: "red",
  high: "red",
  medium: "amber",
  low: "gray",
  active: "green",
  awaiting_human: "amber",
  published: "green",
  pending_review: "amber",
  draft: "gray",
};

export function Badge({ children, tone }: { children: ReactNode; tone?: Tone }) {
  const resolved = tone ?? STATUS_TONE[String(children)] ?? "gray";
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${TONES[resolved]}`}>
      {children}
    </span>
  );
}
