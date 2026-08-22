import { apiClient } from "@/shared/api/client";

export interface InspectResult {
  filename: string;
  source_type: string;
  sheets: string[] | null;
}

export interface UploadedDoc {
  id: string;
  filename: string;
  source_type: string;
  sheet: string | null;
  chunk_count: number;
}

export interface DocHit {
  chunk_id: string;
  document_id: string;
  filename: string;
  source_type: string;
  location: string;
  text: string;
  score: number;
  summary: string;
}

export interface SearchResult {
  query: string;
  hits: DocHit[];
}

export const docsearchApi = {
  inspect: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient
      .post<InspectResult>("/docsearch/inspect", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  upload: (file: File, sheet?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (sheet) form.append("sheet", sheet);
    return apiClient
      .post<UploadedDoc>("/docsearch/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  addUrl: (url: string) =>
    apiClient.post<UploadedDoc>("/docsearch/url", { url }).then((r) => r.data),
  list: () =>
    apiClient.get<{ documents: UploadedDoc[] }>("/docsearch/documents").then((r) => r.data.documents),
  remove: (id: string) =>
    apiClient.delete(`/docsearch/documents/${id}`).then((r) => r.data),
  search: (query: string) =>
    apiClient.post<SearchResult>("/docsearch/search", { query }).then((r) => r.data),
};
