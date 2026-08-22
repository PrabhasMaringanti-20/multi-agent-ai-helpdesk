import { useAuth } from "@/modules/auth/useAuth";
import { Badge } from "@/shared/ui/Badge";

const ROLE_LABEL: Record<string, string> = {
  admin: "Administrator",
  support_engineer: "Support Engineer",
  sme_reviewer: "SME Reviewer",
  end_user: "End User",
};

export function ProfilePage() {
  const { user } = useAuth();
  const email = user?.email ?? "";
  const initials = email.slice(0, 2).toUpperCase();
  const roleLabel = user ? ROLE_LABEL[user.role] ?? user.role : "";

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-lg font-bold text-slate-900">Profile</h1>
      <div className="space-y-6 rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-brand-600 text-lg font-bold text-white">
            {initials}
          </div>
          <div>
            <div className="text-base font-semibold text-slate-900">{email.split("@")[0]}</div>
            <div className="text-sm text-slate-500">{email}</div>
          </div>
        </div>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">Email</dt>
            <dd className="text-sm text-slate-800">{email}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">Role</dt>
            <dd className="text-sm text-slate-800">
              <Badge tone="indigo">{roleLabel}</Badge>
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">Organization</dt>
            <dd className="text-sm text-slate-800">Acme Corp (acme)</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">Status</dt>
            <dd className="text-sm text-emerald-600">Active</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
