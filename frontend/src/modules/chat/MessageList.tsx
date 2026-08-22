import type { Citation } from "@/shared/api/types";
import { FeedbackButtons } from "@/modules/feedback/FeedbackButtons";
import { ThinkingSteps } from "@/modules/chat/ThinkingSteps";
import { Markdown } from "@/shared/ui/Markdown";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  streaming?: boolean;
  tier?: string; // L1 self-service | L2 assisted | L3 human handoff
}

const TIER_META: Record<string, { label: string; className: string }> = {
  L1: { label: "L1 · Self-service", className: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  L2: { label: "L2 · Assisted resolution", className: "bg-amber-50 text-amber-700 ring-amber-200" },
  L3: { label: "L3 · Human handoff", className: "bg-rose-50 text-rose-700 ring-rose-200" },
};

function TierBadge({ tier }: { tier?: string }) {
  const meta = tier ? TIER_META[tier] : undefined;
  if (!meta) return null;
  return (
    <span
      className={`mb-1 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${meta.className}`}
      title="Support tier that handled this answer"
    >
      {meta.label}
    </span>
  );
}

export function MessageList({
  turns,
  conversationId,
}: {
  turns: ChatTurn[];
  conversationId?: string;
}) {
  return (
    <div className="flex flex-col gap-4">
      {turns.map((turn) => (
        <div
          key={turn.id}
          className={`flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm ${
              turn.role === "user"
                ? "bg-brand-600 text-white"
                : "border border-slate-200 bg-white text-slate-800"
            }`}
          >
            {turn.role === "assistant" && !turn.streaming && turn.tier && (
              <div>
                <TierBadge tier={turn.tier} />
              </div>
            )}
            {turn.role === "assistant" && turn.streaming && !turn.content ? (
              <ThinkingSteps />
            ) : turn.role === "assistant" && !turn.streaming ? (
              <Markdown content={turn.content} />
            ) : (
              <p className="whitespace-pre-wrap">
                {turn.content}
                {turn.streaming && <span className="ml-1 animate-pulse">▋</span>}
              </p>
            )}
            {turn.citations && turn.citations.length > 0 && (
              <div className="mt-2 border-t border-slate-100 pt-2 text-xs text-slate-500">
                <span className="font-medium">Sources:</span>{" "}
                {turn.citations.map((c, i) => (
                  <span key={c.chunk_id} className="mr-2">
                    [{i + 1}] {c.source_uri ?? c.doc_id.slice(0, 8)}
                  </span>
                ))}
              </div>
            )}
            {turn.role === "assistant" && !turn.streaming && turn.content && (
              <FeedbackButtons conversationId={conversationId} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
