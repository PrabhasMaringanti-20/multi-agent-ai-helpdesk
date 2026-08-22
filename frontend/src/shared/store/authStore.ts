import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { UserResponse } from "@/shared/api/types";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: UserResponse | null;
  orgSlug: string | null;
  setTokens: (accessToken: string, refreshToken: string) => void;
  setUser: (user: UserResponse | null) => void;
  setOrgSlug: (slug: string) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      orgSlug: null,
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      setUser: (user) => set({ user }),
      setOrgSlug: (orgSlug) => set({ orgSlug }),
      clear: () => set({ accessToken: null, refreshToken: null, user: null }),
    }),
    {
      name: "helpdesk-auth",
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
        orgSlug: state.orgSlug,
      }),
    },
  ),
);

export const selectIsAuthenticated = (state: AuthState): boolean => Boolean(state.accessToken);
