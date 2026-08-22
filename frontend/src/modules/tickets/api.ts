import { apiClient } from "@/shared/api/client";
import type { Page, Ticket } from "@/shared/api/types";

export interface TicketMessage {
  id: string;
  sender_role: string;
  sender_email?: string | null;
  text: string;
  created_at?: string | null;
}

export interface TicketDetail extends Ticket {
  escalation_reason?: string;
  final_confidence?: number | null;
  assigned_engineer_id?: string | null;
  created_by_user_id?: string;
  intake_fields?: Record<string, unknown>;
}

export interface TicketStats {
  total: number;
  open: number;
  resolved: number;
  urgent: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  daily: { date: string; count: number }[];
}

export const ticketsApi = {
  list: (page: number) =>
    apiClient.get<Page<Ticket>>("/tickets", { params: { page } }).then((r) => r.data),
  stats: () => apiClient.get<TicketStats>("/tickets/stats").then((r) => r.data),
  get: (id: string) =>
    apiClient.get<TicketDetail>(`/tickets/${id}`).then((r) => r.data),
  messages: (id: string) =>
    apiClient.get<TicketMessage[]>(`/tickets/${id}/messages`).then((r) => r.data),
  postMessage: (id: string, text: string) =>
    apiClient.post<TicketMessage>(`/tickets/${id}/messages`, { text }).then((r) => r.data),
  suggestReply: (id: string) =>
    apiClient
      .post<{ suggestion: string }>(`/tickets/${id}/suggest-reply`)
      .then((r) => r.data.suggestion),
};
