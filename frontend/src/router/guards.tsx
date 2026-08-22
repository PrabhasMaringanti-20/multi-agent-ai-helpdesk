import type { JSX } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { selectIsAuthenticated, useAuthStore } from "@/shared/store/authStore";

export function ProtectedRoute({ children }: { children: JSX.Element }) {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const location = useLocation();
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return children;
}

export function RoleRoute({ roles, children }: { roles: string[]; children: JSX.Element }) {
  const user = useAuthStore((s) => s.user);
  if (!user || !roles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }
  return children;
}
