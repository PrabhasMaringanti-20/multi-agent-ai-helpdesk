import { apiClient } from "@/shared/api/client";

export interface AnalyticsSummary {
  counts: Record<string, number>;
}

export const analyticsApi = {
  summary: () =>
    apiClient.get<AnalyticsSummary>("/analytics/summary").then((r) => r.data),
};
