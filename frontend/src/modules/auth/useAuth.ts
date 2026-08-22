import { useMutation } from "@tanstack/react-query";

import { authApi } from "@/modules/auth/api";
import { problemMessage } from "@/shared/api/client";
import { queryClient } from "@/shared/api/queryClient";
import type { LoginRequest, RegisterRequest } from "@/shared/api/types";
import { useAuthStore } from "@/shared/store/authStore";
import { toast } from "@/shared/store/uiStore";

export function useAuth() {
  const { user, accessToken, refreshToken, setTokens, setUser, setOrgSlug, clear } =
    useAuthStore();

  const login = useMutation({
    mutationFn: async (body: LoginRequest) => {
      const tokens = await authApi.login(body);
      setTokens(tokens.access_token, tokens.refresh_token);
      setOrgSlug(body.org_slug);
      const me = await authApi.me();
      setUser(me);
      return me;
    },
    onError: (error) => toast.error(problemMessage(error)),
  });

  const register = useMutation({
    mutationFn: (body: RegisterRequest) => authApi.register(body),
    onSuccess: () => toast.success("Account created — please sign in."),
    onError: (error) => toast.error(problemMessage(error)),
  });

  const logout = async () => {
    try {
      if (refreshToken) await authApi.logout(refreshToken);
    } catch {
      /* best-effort */
    }
    clear();
    queryClient.clear();
  };

  return {
    user,
    isAuthenticated: Boolean(accessToken),
    login,
    register,
    logout,
  };
}
