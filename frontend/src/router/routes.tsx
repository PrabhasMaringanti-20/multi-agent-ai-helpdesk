import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "@/layout/AppLayout";
import { LoginPage } from "@/modules/auth/LoginPage";
import { RegisterPage } from "@/modules/auth/RegisterPage";
import { AdminPage } from "@/modules/admin/AdminPage";
import { AiDataPage } from "@/modules/ai-data/AiDataPage";
import { AnalyticsPage } from "@/modules/analytics/AnalyticsPage";
import { ChatPage } from "@/modules/chat/ChatPage";
import { DocumentSearchPage } from "@/modules/docsearch/DocumentSearchPage";
import { DashboardPage } from "@/modules/dashboard/DashboardPage";
import { KnowledgeBasePage } from "@/modules/knowledge-base/KnowledgeBasePage";
import { NotificationsPage } from "@/modules/notifications/NotificationsPage";
import { ProfilePage } from "@/modules/profile/ProfilePage";
import { TicketDetailPage } from "@/modules/tickets/TicketDetailPage";
import { TicketsPage } from "@/modules/tickets/TicketsPage";
import { ProtectedRoute, RoleRoute } from "@/router/guards";

const KB_ROLES = ["support_engineer", "sme_reviewer", "admin"];

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/tickets" element={<TicketsPage />} />
        <Route path="/tickets/:id" element={<TicketDetailPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route
          path="/kb"
          element={
            <RoleRoute roles={KB_ROLES}>
              <KnowledgeBasePage />
            </RoleRoute>
          }
        />
        <Route
          path="/analytics"
          element={
            <RoleRoute roles={["admin"]}>
              <AnalyticsPage />
            </RoleRoute>
          }
        />
        <Route
          path="/ai-data"
          element={
            <RoleRoute roles={KB_ROLES}>
              <AiDataPage />
            </RoleRoute>
          }
        />
        <Route
          path="/docsearch"
          element={
            <RoleRoute roles={KB_ROLES}>
              <DocumentSearchPage />
            </RoleRoute>
          }
        />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route
          path="/admin"
          element={
            <RoleRoute roles={["admin"]}>
              <AdminPage />
            </RoleRoute>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
