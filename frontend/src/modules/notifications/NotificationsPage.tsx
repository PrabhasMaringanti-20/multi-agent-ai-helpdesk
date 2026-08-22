import { useMutation, useQuery } from "@tanstack/react-query";

import { notificationsApi } from "@/modules/notifications/api";
import { queryClient } from "@/shared/api/queryClient";
import { queryKeys } from "@/shared/api/queryKeys";
import { usePagination } from "@/shared/hooks/usePagination";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Spinner } from "@/shared/ui/Spinner";

export function NotificationsPage() {
  const pagination = usePagination();
  const query = useQuery({
    queryKey: queryKeys.notifications(pagination.page),
    queryFn: () => notificationsApi.list(pagination.page),
  });

  const markRead = useMutation({
    mutationFn: (id: string) => notificationsApi.markRead(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications(pagination.page) }),
  });

  return (
    <div>
      <PageHeader title="Notifications" subtitle="Assignments, handoffs, and SLA alerts" />
      <Card>
        {query.isLoading ? (
          <Spinner label="Loading…" />
        ) : query.isError ? (
          <EmptyState title="Notifications service unavailable" hint="The /notifications endpoint responds once deployed." />
        ) : !query.data || query.data.items.length === 0 ? (
          <EmptyState title="You're all caught up" />
        ) : (
          <ul className="divide-y divide-slate-100">
            {query.data.items.map((n) => (
              <li key={n.id} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium text-slate-800">{n.type}</p>
                  <p className="text-xs text-slate-400">{new Date(n.created_at).toLocaleString()}</p>
                </div>
                <div className="flex items-center gap-3">
                  <Badge>{n.status}</Badge>
                  {n.status !== "read" && (
                    <Button variant="ghost" size="sm" onClick={() => markRead.mutate(n.id)}>
                      Mark read
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
