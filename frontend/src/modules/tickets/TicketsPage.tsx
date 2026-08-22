import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { ticketsApi } from "@/modules/tickets/api";
import { queryKeys } from "@/shared/api/queryKeys";
import { usePagination } from "@/shared/hooks/usePagination";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Spinner } from "@/shared/ui/Spinner";

export function TicketsPage() {
  const pagination = usePagination();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: queryKeys.tickets("all", pagination.page),
    queryFn: () => ticketsApi.list(pagination.page),
  });

  return (
    <div>
      <PageHeader title="Tickets" subtitle="Escalated support requests" />
      <Card>
        {query.isLoading ? (
          <Spinner label="Loading tickets…" />
        ) : query.isError ? (
          <EmptyState title="Tickets service unavailable" hint="The /tickets endpoint responds once deployed." />
        ) : !query.data || query.data.items.length === 0 ? (
          <EmptyState title="No tickets yet" hint="Escalations from the AI will appear here." />
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-400">
              <tr>
                <th className="py-2">Subject</th>
                <th>Category</th>
                <th>Priority</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((ticket) => (
                <tr
                  key={ticket.id}
                  onClick={() => navigate(`/tickets/${ticket.id}`)}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                >
                  <td className="py-2 font-medium text-brand-700">{ticket.subject}</td>
                  <td>{ticket.category}</td>
                  <td><Badge>{ticket.priority}</Badge></td>
                  <td><Badge>{ticket.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={pagination.prev} disabled={pagination.page === 1}>
            Previous
          </Button>
          <Button variant="ghost" size="sm" onClick={pagination.next}>
            Next
          </Button>
        </div>
      </Card>
    </div>
  );
}
