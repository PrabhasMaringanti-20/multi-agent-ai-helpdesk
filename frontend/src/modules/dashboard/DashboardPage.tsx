import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { analyticsApi } from "@/modules/analytics/api";
import { useAuth } from "@/modules/auth/useAuth";
import { notificationsApi } from "@/modules/notifications/api";
import { type TicketStats, ticketsApi } from "@/modules/tickets/api";
import { queryKeys } from "@/shared/api/queryKeys";
import { Badge } from "@/shared/ui/Badge";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Spinner } from "@/shared/ui/Spinner";

const STATUS_COLOR: Record<string, string> = {
  open: "bg-amber-400", triaged: "bg-blue-400", in_progress: "bg-indigo-400",
  awaiting_user: "bg-purple-400", resolved: "bg-emerald-400", closed: "bg-slate-400",
  reopened: "bg-rose-400",
};

function StatCard({ label, value, accent }: { label: string; value: number | string; accent?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className={`text-2xl font-bold ${accent ?? "text-slate-900"}`}>{value}</p>
      <p className="mt-1 text-xs text-slate-500">{label}</p>
    </div>
  );
}

function ActionTile({ to, title, hint, icon }: { to: string; title: string; hint: string; icon: string }) {
  return (
    <Link to={to}>
      <Card className="h-full transition hover:border-brand-300 hover:shadow-md">
        <div className="text-xl">{icon}</div>
        <p className="mt-1 text-sm font-semibold text-slate-800">{title}</p>
        <p className="mt-0.5 text-xs text-slate-500">{hint}</p>
      </Card>
    </Link>
  );
}

function StatusBars({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map((e) => e[1]));
  if (entries.length === 0) return <EmptyState title="No tickets yet" hint="" />;
  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center gap-2 text-xs">
          <span className="w-24 shrink-0 capitalize text-slate-500">{k.replace(/_/g, " ")}</span>
          <div className="h-2.5 flex-1 overflow-hidden rounded bg-slate-100">
            <div className={`h-full rounded ${STATUS_COLOR[k] ?? "bg-brand-400"}`} style={{ width: `${(v / max) * 100}%` }} />
          </div>
          <span className="w-6 text-right font-medium text-slate-600">{v}</span>
        </div>
      ))}
    </div>
  );
}

function TrendBars({ daily }: { daily: { date: string; count: number }[] }) {
  const max = Math.max(1, ...daily.map((d) => d.count));
  return (
    <div className="flex items-end gap-2" style={{ height: 120 }}>
      {daily.map((d) => (
        <div key={d.date} className="flex flex-1 flex-col items-center justify-end gap-1">
          <span className="text-[10px] font-medium text-slate-600">{d.count}</span>
          <div
            className="w-full rounded-t bg-brand-400"
            style={{ height: `${d.count ? Math.max((d.count / max) * 84, 4) : 2}px` }}
          />
          <span className="text-[9px] text-slate-400">{d.date.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}

function useStats() {
  return useQuery({ queryKey: ["tickets", "stats"], queryFn: ticketsApi.stats });
}

function KpiRow({ stats, cards }: { stats?: TicketStats; cards: { key: keyof TicketStats; label: string; accent?: string }[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cards.map((c) => (
        <StatCard key={String(c.key)} label={c.label} accent={c.accent} value={(stats?.[c.key] as number) ?? 0} />
      ))}
    </div>
  );
}

function RecentTickets({ title, emptyHint }: { title: string; emptyHint: string }) {
  const q = useQuery({ queryKey: queryKeys.tickets("dashboard", 1), queryFn: () => ticketsApi.list(1) });
  const items = q.data?.items ?? [];
  return (
    <Card title={title} actions={<Link className="text-xs font-medium text-brand-600" to="/tickets">View all</Link>}>
      {q.isLoading ? (
        <Spinner />
      ) : items.length === 0 ? (
        <EmptyState title="No tickets" hint={emptyHint} />
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.slice(0, 6).map((t) => (
            <li key={t.id}>
              <Link to={`/tickets/${t.id}`} className="flex items-center justify-between py-2 text-sm hover:bg-slate-50">
                <span className="truncate pr-2 text-slate-700">{t.subject}</span>
                <span className="flex shrink-0 gap-1"><Badge>{t.priority}</Badge><Badge>{t.status}</Badge></span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function MyTicketsCard({ title }: { title: string }) {
  const stats = useStats();
  return (
    <Card title={title} actions={<Link className="text-xs font-medium text-brand-600" to="/tickets">View all</Link>}>
      <div className="mb-3 grid grid-cols-2 gap-3">
        <StatCard label="Total" value={stats.data?.total ?? 0} />
        <StatCard label="Open / active" value={stats.data?.open ?? 0} />
      </div>
      <RecentTicketsList />
    </Card>
  );
}

function RecentTicketsList() {
  const q = useQuery({ queryKey: queryKeys.tickets("dashboard", 1), queryFn: () => ticketsApi.list(1) });
  const items = q.data?.items ?? [];
  if (q.isLoading) return <Spinner />;
  if (items.length === 0)
    return <EmptyState title="No tickets yet" hint="When the AI escalates an issue, your ticket shows here." />;
  return (
    <ul className="divide-y divide-slate-100">
      {items.slice(0, 5).map((t) => (
        <li key={t.id}>
          <Link to={`/tickets/${t.id}`} className="flex items-center justify-between py-2 text-sm hover:bg-slate-50">
            <span className="truncate pr-2 text-slate-700">{t.subject}</span>
            <Badge>{t.status}</Badge>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function NotificationsCard() {
  const q = useQuery({ queryKey: ["notifications", 1], queryFn: () => notificationsApi.list(1) });
  const items = q.data?.items ?? [];
  return (
    <Card title="Recent notifications" actions={<Link className="text-xs font-medium text-brand-600" to="/notifications">View all</Link>}>
      {q.isLoading ? (
        <Spinner />
      ) : items.length === 0 ? (
        <EmptyState title="You're all caught up" hint="Updates appear here." />
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.slice(0, 5).map((n) => {
            const unread = n.status !== "read";
            const payload = (n.payload ?? {}) as { title?: string; body?: string };
            return (
              <li key={n.id} className="flex items-start gap-2 py-2 text-sm">
                <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${unread ? "bg-brand-500" : "bg-slate-200"}`} />
                <div>
                  <p className={unread ? "font-medium text-slate-800" : "text-slate-600"}>{payload.title ?? n.type}</p>
                  {payload.body && <p className="text-xs text-slate-400">{payload.body}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/* ---------------- role dashboards ---------------- */
function AdminDashboard() {
  const stats = useStats();
  const analytics = useQuery({ queryKey: queryKeys.analytics, queryFn: analyticsApi.summary });
  const counts = analytics.data?.counts ?? {};
  const alabels: Record<string, string> = {
    chat_started: "Chats", answer_delivered: "Answers", auto_resolved: "Auto-resolved",
    escalated: "Escalated", clarification_requested: "Clarifications",
    ticket_created: "Tickets created", ticket_resolved: "Tickets resolved",
    feedback_positive: "👍", feedback_negative: "👎",
  };
  return (
    <div className="space-y-6">
      <KpiRow stats={stats.data} cards={[
        { key: "total", label: "Total tickets" },
        { key: "open", label: "Open / active", accent: "text-amber-600" },
        { key: "urgent", label: "Urgent", accent: "text-rose-600" },
        { key: "resolved", label: "Resolved", accent: "text-emerald-600" },
      ]} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Tickets by status">
          {stats.isLoading ? <Spinner /> : <StatusBars data={stats.data?.by_status ?? {}} />}
        </Card>
        <Card title="Tickets created — last 7 days">
          {stats.isLoading ? <Spinner /> : <TrendBars daily={stats.data?.daily ?? []} />}
        </Card>
      </div>
      <Card title="AI activity">
        {analytics.isLoading ? (
          <Spinner />
        ) : Object.keys(counts).length === 0 ? (
          <EmptyState title="No activity yet" hint="Metrics appear as users chat." />
        ) : (
          <div className="flex flex-wrap gap-2">
            {Object.entries(counts).map(([k, v]) => (
              <span key={k} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                <span className="font-semibold text-slate-800">{v}</span> {alabels[k] ?? k}
              </span>
            ))}
          </div>
        )}
      </Card>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <RecentTickets title="Recent tickets" emptyHint="Escalations across the org appear here." />
        <NotificationsCard />
      </div>
    </div>
  );
}

function EngineerDashboard() {
  const stats = useStats();
  return (
    <div className="space-y-6">
      <KpiRow stats={stats.data} cards={[
        { key: "open", label: "Open / active", accent: "text-amber-600" },
        { key: "urgent", label: "Urgent", accent: "text-rose-600" },
        { key: "total", label: "Total tickets" },
        { key: "resolved", label: "Resolved", accent: "text-emerald-600" },
      ]} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Queue by status">
          {stats.isLoading ? <Spinner /> : <StatusBars data={stats.data?.by_status ?? {}} />}
        </Card>
        <RecentTickets title="Support queue" emptyHint="Tickets routed to your team appear here." />
      </div>
      <NotificationsCard />
    </div>
  );
}

function UserDashboard() {
  const navigate = useNavigate();
  const suggestions = [
    "How do I reset my password?",
    "VPN error 800 on GlobalProtect",
    "Outlook is stuck connecting",
    "How do I set up MFA on a new phone?",
  ];
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <ActionTile to="/chat" title="Ask the AI" hint="Instant, grounded answers" icon="💬" />
        <ActionTile to="/tickets" title="My Tickets" hint="Track your requests" icon="🎫" />
        <ActionTile to="/notifications" title="Notifications" hint="Updates on your issues" icon="🔔" />
        <ActionTile to="/profile" title="My Profile" hint="Your account details" icon="👤" />
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Need help? Ask the AI">
          <p className="text-sm text-slate-600">
            Describe your IT issue in plain English and get a step-by-step answer. Try one of these:
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button
                key={s}
                onClick={() => navigate(`/chat?q=${encodeURIComponent(s)}`)}
                className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700 hover:bg-brand-100"
              >
                {s}
              </button>
            ))}
          </div>
          <Link to="/chat" className="mt-4 inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Open AI Chat →
          </Link>
        </Card>
        <MyTicketsCard title="My tickets" />
      </div>
      <NotificationsCard />
    </div>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const role = user?.role;
  const subtitle =
    role === "admin" ? "Administrator overview"
    : role === "support_engineer" || role === "sme_reviewer" ? "Support engineer workspace"
    : "Your IT support at a glance";

  return (
    <div>
      <PageHeader title="Home" subtitle={subtitle} />
      {role === "admin" ? <AdminDashboard />
        : role === "support_engineer" || role === "sme_reviewer" ? <EngineerDashboard />
        : <UserDashboard />}
    </div>
  );
}
