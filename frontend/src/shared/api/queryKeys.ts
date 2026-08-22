// Centralized React Query keys for cache consistency + targeted invalidation.
export const queryKeys = {
  tickets: (queue: string, page: number) => ["tickets", queue, page] as const,
  notifications: (page: number) => ["notifications", page] as const,
  analytics: ["analytics"] as const,
};
