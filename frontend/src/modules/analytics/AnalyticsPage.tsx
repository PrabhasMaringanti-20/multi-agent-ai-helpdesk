import { useQuery } from "@tanstack/react-query";

import { analyticsApi } from "@/modules/analytics/api";
import { queryKeys } from "@/shared/api/queryKeys";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Spinner } from "@/shared/ui/Spinner";

const LABELS: Record<string, string> = {
  chat_answered: "Chats answered",
  auto_resolved: "Auto-resolved",
  escalated: "Escalated",
  feedback_given: "Feedback given",
  kb_published: "KB published",
};

export function AnalyticsPage() {
  const query = useQuery({ queryKey: queryKeys.analytics, queryFn: analyticsApi.summary });
  const counts = query.data?.counts ?? {};

  return (
    <div>
      <PageHeader title="Analytics" subtitle="Deflection, escalation, and KB metrics" />
      {query.isLoading ? (
        <Spinner label="Loading metrics…" />
      ) : query.isError ? (
        <EmptyState title="Analytics service unavailable" hint="The /analytics endpoint responds once deployed." />
      ) : Object.keys(counts).length === 0 ? (
        <EmptyState title="No analytics yet" hint="Metrics populate as conversations occur." />
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Object.entries(counts).map(([key, value]) => (
            <Card key={key}>
              <p className="text-2xl font-bold text-slate-900">{value}</p>
              <p className="mt-1 text-xs text-slate-500">{LABELS[key] ?? key}</p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
