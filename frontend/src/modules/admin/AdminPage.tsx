import { Card } from "@/shared/ui/Card";
import { PageHeader } from "@/shared/ui/PageHeader";

// The canonical seed categories (category_registry) — the data-driven routing seam.
const CATEGORIES = [
  { key: "login_issue", queue: "identity_access", sla: "standard" },
  { key: "password_reset", queue: "identity_access", sla: "standard" },
  { key: "vpn", queue: "network_access", sla: "standard" },
  { key: "payment", queue: "billing", sla: "priority" },
  { key: "software_install", queue: "endpoint_support", sla: "standard" },
  { key: "application_error", queue: "app_support", sla: "standard" },
  { key: "email", queue: "messaging", sla: "standard" },
  { key: "hardware_request", queue: "asset_management", sla: "standard" },
];

const ROLES = ["end_user", "support_engineer", "sme_reviewer", "admin"];

export function AdminPage() {
  return (
    <div className="space-y-6">
      <PageHeader title="Administration" subtitle="Roles, categories, and platform configuration" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="RBAC roles">
          <ul className="space-y-1 text-sm text-slate-700">
            {ROLES.map((role) => (
              <li key={role} className="rounded bg-slate-50 px-3 py-1.5">{role}</li>
            ))}
          </ul>
        </Card>
        <Card title="Category registry">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-400">
              <tr>
                <th className="py-1">Category</th>
                <th>Queue</th>
                <th>SLA</th>
              </tr>
            </thead>
            <tbody>
              {CATEGORIES.map((c) => (
                <tr key={c.key} className="border-t border-slate-100">
                  <td className="py-1.5 font-medium text-slate-800">{c.key}</td>
                  <td>{c.queue}</td>
                  <td>{c.sla}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
