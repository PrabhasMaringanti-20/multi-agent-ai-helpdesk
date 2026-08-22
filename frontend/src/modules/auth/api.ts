import { apiClient } from "@/shared/api/client";
import type {
  LoginRequest,
  MessageResponse,
  RegisterRequest,
  TokenResponse,
  UserResponse,
} from "@/shared/api/types";

export const authApi = {
  login: (body: LoginRequest) =>
    apiClient.post<TokenResponse>("/auth/login", body).then((r) => r.data),
  register: (body: RegisterRequest) =>
    apiClient.post<UserResponse>("/auth/register", body).then((r) => r.data),
  me: () => apiClient.get<UserResponse>("/auth/me").then((r) => r.data),
  logout: (refresh_token: string) =>
    apiClient.post<MessageResponse>("/auth/logout", { refresh_token }).then((r) => r.data),
};
