import { apiClient } from "@/shared/api/client";

export interface AiDataResult {
  answer: string;
  tool: string;
  args: Record<string, unknown>;
  planner: string; // "llm" | "keyword-fallback"
  result: Record<string, unknown>;
}

export const aiDataApi = {
  query: (instruction: string) =>
    apiClient.post<AiDataResult>("/ai/query", { instruction }).then((r) => r.data),
};
