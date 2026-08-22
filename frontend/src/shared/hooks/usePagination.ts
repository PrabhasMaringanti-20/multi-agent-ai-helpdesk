import { useState } from "react";

export interface Pagination {
  page: number;
  size: number;
  next: () => void;
  prev: () => void;
  setPage: (page: number) => void;
}

export function usePagination(initialSize = 20): Pagination {
  const [page, setPage] = useState(1);
  return {
    page,
    size: initialSize,
    next: () => setPage((p) => p + 1),
    prev: () => setPage((p) => Math.max(1, p - 1)),
    setPage,
  };
}
