import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@/modules/auth/useAuth";
import { notificationsApi } from "@/modules/notifications/api";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";

const ROLE_LABEL: Record<string, string> = {
  admin: "Administrator",
  support_engineer: "Support Engineer",
  sme_reviewer: "SME Reviewer",
  end_user: "End User",
};

export function Topbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const { data: unread = 0 } = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: notificationsApi.unreadCount,
    refetchInterval: 20000,
    enabled: Boolean(user),
  });

  const onLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const firstName = user?.email ? user.email.split("@")[0] : "there";
  const roleLabel = user ? ROLE_LABEL[user.role] ?? user.role : "";

  return (
    <header className="flex h-14 items-center justify-between border-b border-slate-200 bg-white px-6">
      <div>
        <div className="text-sm font-semibold text-slate-800">Welcome, {firstName}</div>
        <div className="text-xs text-slate-400">Role: {roleLabel}</div>
      </div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => navigate("/notifications")}
          className="relative rounded-lg p-2 text-slate-500 hover:bg-slate-50"
          aria-label="Notifications"
          title="Notifications"
        >
          <span className="text-lg leading-none">🔔</span>
          {unread > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </button>
        {user && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-slate-600">{user.email}</span>
            <Badge tone="indigo">{roleLabel}</Badge>
          </div>
        )}
        <Button variant="secondary" size="sm" onClick={onLogout}>
          Sign out
        </Button>
      </div>
    </header>
  );
}
