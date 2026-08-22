import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { MessageList, type ChatTurn } from "@/modules/chat/MessageList";
import { Composer } from "@/modules/chat/Composer";
import { streamChat } from "@/shared/api/sse";
import type { Citation } from "@/shared/api/types";
import { toast } from "@/shared/store/uiStore";
import { PageHeader } from "@/shared/ui/PageHeader";

function newId(): string {
  return crypto.randomUUID();
}

export function ChatPage() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [quickReplies, setQuickReplies] = useState<string[]>([]);
  const threadId = useMemo(() => newId(), []);
  const abortRef = useRef<AbortController | null>(null);
  const [searchParams] = useSearchParams();
  const autoSent = useRef(false);

  const patchAssistant = (id: string, patch: Partial<ChatTurn>) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const send = async (text: string) => {
    const userTurn: ChatTurn = { id: newId(), role: "user", content: text };
    const assistantId = newId();
    setQuickReplies([]);
    setTurns((prev) => [
      ...prev,
      userTurn,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    await streamChat(
      { message: text, thread_id: threadId },
      {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "token") {
            const token = String(event.data.text ?? "");
            setTurns((prev) =>
              prev.map((t) =>
                t.id === assistantId ? { ...t, content: t.content + token } : t,
              ),
            );
          } else if (event.type === "citations") {
            patchAssistant(assistantId, {
              citations: (event.data.citations as Citation[]) ?? [],
            });
          } else if (event.type === "quick_replies") {
            setQuickReplies((event.data.options as string[]) ?? []);
          } else if (event.type === "decision") {
            const tier = event.data.tier as string | undefined;
            if (tier) patchAssistant(assistantId, { tier });
          } else if (event.type === "done") {
            patchAssistant(assistantId, { streaming: false });
          } else if (event.type === "error") {
            patchAssistant(assistantId, {
              streaming: false,
              content: "Sorry — something went wrong handling that request.",
            });
            toast.error(String(event.data.message ?? "Chat error"));
          }
        },
        onError: (error) => {
          patchAssistant(assistantId, {
            streaming: false,
            content:
              "The chat service is not reachable yet. (The /chat/messages endpoint is served once the backend chat router is deployed.)",
          });
          toast.error(error.message);
        },
      },
    );

    patchAssistant(assistantId, { streaming: false });
    setStreaming(false);
    abortRef.current = null;
  };

  // Auto-send a question passed from the dashboard suggestions (/chat?q=...).
  useEffect(() => {
    const q = searchParams.get("q");
    if (q && !autoSent.current) {
      autoSent.current = true;
      void send(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cancel = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      <PageHeader title="AI Chat" subtitle="Ask a question — grounded answers with citations." />
      <div className="flex flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
        <div className="flex-1 overflow-y-auto p-4">
          {turns.length === 0 ? (
            <p className="mt-12 text-center text-sm text-slate-400">
              Start a conversation — e.g. “I can’t connect to the VPN”.
            </p>
          ) : (
            <MessageList turns={turns} conversationId={threadId} />
          )}
        </div>
        {quickReplies.length > 0 && !streaming && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 bg-white px-4 py-2">
            <span className="text-xs text-slate-400">Quick replies:</span>
            {quickReplies.map((qr) => {
              const isOther = /^(other|something else)$/i.test(qr.trim());
              return (
                <button
                  key={qr}
                  onClick={() => {
                    setQuickReplies([]);
                    if (!isOther) send(qr);
                  }}
                  className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100"
                >
                  {qr}
                </button>
              );
            })}
          </div>
        )}
        <Composer disabled={streaming} streaming={streaming} onSend={send} onCancel={cancel} />
      </div>
    </div>
  );
}
