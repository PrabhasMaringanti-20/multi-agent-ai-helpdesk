import { apiClient } from "@/shared/api/client";
import type { NotificationDTO, Page } from "@/shared/api/types";

export const notificationsApi = {
  list: (page: number) =>
    apiClient
      .get<Page<NotificationDTO>>("/notifications", { params: { page } })
      .then((r) => r.data),
  markRead: (id: string) =>
    apiClient.post(`/notifications/${id}/read`).then((r) => r.data),
  unreadCount: () =>
    apiClient.get<{ count: number }>("/notifications/unread-count").then((r) => r.data.count),
};
