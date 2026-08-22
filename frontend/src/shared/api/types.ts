// DTOs mirroring the backend Pydantic schemas (ARCHITECTURE.md §9).

export interface UserResponse {
  id: string;
  org_id: string;
  email: string;
  full_name: string | null;
  role: string;
  locale: string | null;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginRequest {
  org_slug: string;
  email: string;
  password: string;
}

export interface RegisterRequest {
  org_slug: string;
  email: string;
  password: string;
  full_name?: string | null;
  locale?: string | null;
}

export interface MessageResponse {
  detail: string;
}

export interface ProblemDetail {
  type?: string;
  title: string;
  status: number;
  detail?: string | null;
  trace_id?: string | null;
  errors?: Array<Record<string, unknown>> | null;
}

export interface PageMeta {
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export interface Citation {
  chunk_id: string;
  doc_id: string;
  source_uri?: string | null;
  version?: number | null;
}

export interface Ticket {
  id: string;
  subject: string;
  category: string;
  priority: string;
  status: string;
  assigned_queue: string;
  created_at?: string;
}

export interface NotificationDTO {
  id: string;
  type: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  category: string;
  doc_status: string;
  version: number;
  last_verified_at: string | null;
}
