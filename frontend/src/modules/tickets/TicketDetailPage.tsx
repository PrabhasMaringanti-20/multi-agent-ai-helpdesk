import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAuth } from "@/modules/auth/useAuth";
import { ticketsApi } from "@/modules/tickets/api";
import { problemMessage } from "@/shared/api/client";
import { queryClient } from "@/shared/api/queryClient";
import { toast } from "@/shared/store/uiStore";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Spinner } from "@/shared/ui/Spinner";

const ENGINEER_ROLES = ["support_engineer", "sme_reviewer", "admin"];

function fmt(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
}

export function TicketDetailPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const isEngineer = ENGINEER_ROLES.includes(user?.role ?? "");
  const mySide = isEngineer ? "engineer" : "user";
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const ticket = useQuery({ queryKey: ["ticket", id], queryFn: () => ticketsApi.get(id), enabled: !!id });
  const messages = useQuery({
    queryKey: ["ticket", id, "messages"],
    queryFn: () => ticketsApi.messages(id),
    enabled: !!id,
    refetchInterval: 5000, // near-real-time polling
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.data]);

  const send = useMutation({
    mutationFn: (t: string) => ticketsApi.postMessage(id, t),
    onSuccess: () => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["ticket", id, "messages"] });
    },
    onError: (e) => toast.error(problemMessage(e)),
  });

  const suggest = useMutation({
    mutationFn: () => ticketsApi.suggestReply(id),
    onSuccess: (s) => setText(s),
    onError: (e) => toast.error(problemMessage(e)),
  });

  const onSend = () => {
    const t = text.trim();
    if (t) send.mutate(t);
  };

  if (ticket.isLoading) return <Spinner label="Loading ticket…" />;
  if (ticket.isError || !ticket.data) {
    return (
      <div className="space-y-3">
        <Link to="/tickets" className="text-xs font-medium text-brand-600">← Back to tickets</Link>
        <p className="text-sm text-slate-500">Ticket not found.</p>
      </div>
    );
  }
  const t = ticket.data;
  const list = messages.data ?? [];

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div>
        <Link to="/tickets" className="text-xs font-medium text-brand-600">← Back to tickets</Link>
        <h1 className="mt-1 text-lg font-bold text-slate-900">{t.subject}</h1>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>{t.category}</span>
          <Badge>{t.priority}</Badge>
          <Badge>{t.status}</Badge>
        </div>
      </div>

      <div className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2 text-xs">
          <span className="font-semibold text-slate-700">Conversation</span>
          <span className="flex items-center gap-1 text-emerald-600">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            {isEngineer ? "User" : "Support engineer"} online
          </span>
        </div>

        <div className="h-[430px] space-y-3 overflow-y-auto bg-slate-50 p-4">
          {messages.isLoading ? (
            <Spinner label="Loading messages…" />
          ) : list.length === 0 ? (
            <p className="pt-10 text-center text-sm text-slate-400">
              No messages yet — start the conversation below.
            </p>
          ) : (
            list.map((m) => {
              const mine = m.sender_role === mySide;
              return (
                <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[75%] rounded-2xl px-4 py-2 text-sm ${
                      mine ? "bg-brand-600 text-white" : "border border-slate-200 bg-white text-slate-800"
                    }`}
                  >
                    <div className={`mb-0.5 text-[10px] ${mine ? "text-brand-100" : "text-slate-400"}`}>
                      {m.sender_role === "engineer" ? "Support Engineer" : "User"} · {fmt(m.created_at)}
                    </div>
                    <div className="whitespace-pre-wrap">{m.text}</div>
                    {mine && <div className="mt-0.5 text-right text-[10px] text-brand-100">✓✓ Read</div>}
                  </div>
                </div>
              );
            })
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t border-slate-100 p-3">
          {isEngineer && (
            <div className="mb-2 flex items-center gap-2">
              <Button variant="ghost" size="sm" loading={suggest.isPending} onClick={() => suggest.mutate()}>
                ✨ AI suggest reply
              </Button>
              <span className="text-[11px] text-slate-400">Drafts a grounded reply from the knowledge base (Gemini)</span>
            </div>
          )}
          <div className="flex items-end gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSend();
                }
              }}
              rows={2}
              placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
              className="flex-1 resize-none rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
            />
            <Button loading={send.isPending} onClick={onSend}>
              Send
            </Button>
          </div>
          <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-300">
            <span>📎</span> Attachments coming soon
          </div>
        </div>
      </div>
    </div>
  );
}
