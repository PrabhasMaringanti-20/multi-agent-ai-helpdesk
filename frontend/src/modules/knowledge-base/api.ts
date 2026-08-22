import { apiClient } from "@/shared/api/client";
import type { KnowledgeDocument, Page } from "@/shared/api/types";

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  retrieval_namespace: string;
  body: string;
}

export interface KbVersion {
  version: number;
  title: string;
  doc_status: string;
  change_summary?: string | null;
  created_at?: string | null;
}

export const knowledgeApi = {
  list: (page: number, q?: string) =>
    apiClient
      .get<Page<KnowledgeDocument>>("/kb/documents", {
        params: { page, ...(q ? { q } : {}) },
      })
      .then((r) => r.data),
  get: (id: string) =>
    apiClient.get<KnowledgeDocumentDetail>(`/kb/documents/${id}`).then((r) => r.data),
  create: (data: { title: string; category: string; body: string }) =>
    apiClient.post<KnowledgeDocumentDetail>("/kb/documents/create", data).then((r) => r.data),
  edit: (id: string, data: { title?: string; body?: string }) =>
    apiClient.patch<KnowledgeDocumentDetail>(`/kb/documents/${id}`, data).then((r) => r.data),
  publish: (id: string) =>
    apiClient.post<KnowledgeDocumentDetail>(`/kb/documents/${id}/publish`).then((r) => r.data),
  unpublish: (id: string) =>
    apiClient.post<KnowledgeDocumentDetail>(`/kb/documents/${id}/unpublish`).then((r) => r.data),
  versions: (id: string) =>
    apiClient.get<KbVersion[]>(`/kb/documents/${id}/versions`).then((r) => r.data),
  upload: (file: File, category: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("category", category);
    return apiClient
      .post("/kb/documents", form, { headers: { "Content-Type": "multipart/form-data" } })
      .then((r) => r.data);
  },
};
