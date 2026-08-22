import { NavLink } from "react-router-dom";

import { useAuth } from "@/modules/auth/useAuth";

interface NavItem {
  to: string;
  label: string;
  roles?: string[]; // when set, only these roles see the item
}

interface NavSection {
  title: string;
  items: NavItem[];
}

const STAFF = ["support_engineer", "sme_reviewer", "admin"];

// Grouped so the menu reads as two short lists instead of one long wall of links.
// End users only ever see "Workspace" (the Tools section filters out for them).
const SECTIONS: NavSection[] = [
  {
    title: "Workspace",
    items: [
      { to: "/", label: "Home" },
      { to: "/chat", label: "AI Chat" },
      { to: "/tickets", label: "My Tickets" },
      { to: "/notifications", label: "Notifications" },
      { to: "/profile", label: "Profile" },
    ],
  },
  {
    title: "Tools",
    items: [
      { to: "/kb", label: "Knowledge Base", roles: STAFF },
      { to: "/docsearch", label: "Document Search", roles: STAFF },
      { to: "/ai-data", label: "AI Data API", roles: STAFF },
      { to: "/analytics", label: "Analytics", roles: ["admin"] },
      { to: "/admin", label: "Admin", roles: ["admin"] },
    ],
  },
];

export function Sidebar() {
  const { user } = useAuth();
  const role = user?.role ?? "end_user";
  const visible = (items: NavItem[]) => items.filter((i) => !i.roles || i.roles.includes(role));

  return (
    <aside className="flex w-60 flex-col border-r border-slate-200 bg-white">
      <div className="flex h-14 items-center gap-2 border-b border-slate-100 px-4">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-xs font-bold text-white">
          AI
        </span>
        <span className="text-sm font-bold text-brand-700">AI Helpdesk</span>
      </div>
      <nav className="flex-1 space-y-4 p-3">
        {SECTIONS.map((section) => {
          const items = visible(section.items);
          if (items.length === 0) return null;
          return (
            <div key={section.title}>
              <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                {section.title}
              </p>
              <div className="space-y-1">
                {items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/"}
                    className={({ isActive }) =>
                      `block rounded-lg px-3 py-2 text-sm font-medium ${
                        isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-50"
                      }`
                    }
                  >
                    {item.label}
                  </NavLink>
                ))}
              </div>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
