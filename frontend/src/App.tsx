import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'
import { ConfirmProvider, ToastProvider } from '@/components/ui'
import Layout from '@/components/Layout'
import LoginPage from '@/pages/LoginPage'
import RegisterPage from '@/pages/RegisterPage'
import DashboardPage from '@/pages/DashboardPage'
import DatasetsPage from '@/pages/DatasetsPage'
import DatasetDetailPage from '@/pages/DatasetDetailPage'
import GeneratePage from '@/pages/GeneratePage'
import TracesPage from '@/pages/TracesPage'
import AutoCollectPage from '@/pages/AutoCollectPage'
import ConfigPage from '@/pages/ConfigPage'
import AuditPage from '@/pages/AuditPage'
import ProjectsPage from '@/pages/ProjectsPage'
import BenchmarkPage from '@/pages/BenchmarkPage'
import EvaluationPage from '@/pages/EvaluationPage'
import EvaluationRunDetailPage from '@/pages/EvaluationRunDetailPage'
import EvaluationComparePage from '@/pages/EvaluationComparePage'
import EvaluatorsPage from '@/pages/EvaluatorsPage'
import EvaluatorComparePage from '@/pages/EvaluatorComparePage'
import EvaluatorProvidersPage from '@/pages/EvaluatorProvidersPage'
import RequestLogPage from '@/pages/RequestLogPage'
// 外部客户 portal（入口反转后 external_customer 的默认视图）
import PortalBatchesPage from '@/pages/portal/PortalBatchesPage'
import PortalBatchDetailPage from '@/pages/portal/PortalBatchDetailPage'
// 内部 admin 租户管理 + 客户反馈回流展示
import TenantsPage from '@/pages/admin/TenantsPage'
import FeedbackReviewPage from '@/pages/FeedbackReviewPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  return <>{children}</>
}

// Role-aware guard. Renders children only when the current user's role is in
// `roles`; otherwise redirects to the dashboard. Assumes it is nested under
// ProtectedRoute, so the user is already authenticated here.
function RoleRoute({ roles, children }: { roles: string[]; children: React.ReactNode }) {
  const role = useAuthStore((s) => s.role)
  if (!roles.includes(role() ?? '')) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

// 入口反转的落地分流：登录后访问 "/" 时按角色决定默认视图。
// external_customer → portal（样例评审），内部角色 → dashboard。
function LandingRedirect() {
  const isExternal = useAuthStore((s) => s.isExternal)
  return <Navigate to={isExternal() ? '/portal' : '/dashboard'} replace />
}

export default function App() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route index element={<LandingRedirect />} />
              {/* 外部客户 portal：受 ProtectedRoute 保护，所有已登录角色可达；
                  external_customer 默认落地于此，admin 也可访问便于排查 */}
              <Route path="portal" element={<PortalBatchesPage />} />
              <Route path="portal/batches/:batchId" element={<PortalBatchDetailPage />} />
              <Route path="dashboard" element={<DashboardPage />} />
              <Route path="datasets" element={<DatasetsPage />} />
              <Route path="datasets/:name" element={<DatasetDetailPage />} />
              <Route path="projects" element={<ProjectsPage />} />
              <Route path="benchmark/:projectId" element={<BenchmarkPage />} />
              <Route path="generate" element={<GeneratePage />} />
              <Route path="traces" element={<TracesPage />} />
              <Route path="evaluation" element={<EvaluationPage />} />
              <Route path="evaluation/compare" element={<EvaluationComparePage />} />
              <Route path="evaluation/runs/:runId" element={<EvaluationRunDetailPage />} />
              <Route path="evaluators" element={<EvaluatorsPage />} />
              <Route path="evaluators/compare" element={<EvaluatorComparePage />} />
              <Route path="auto-collect" element={<AutoCollectPage />} />
              <Route
                path="config"
                element={
                  <RoleRoute roles={['admin']}>
                    <ConfigPage />
                  </RoleRoute>
                }
              />
              <Route
                path="audit"
                element={
                  <RoleRoute roles={['admin']}>
                    <AuditPage />
                  </RoleRoute>
                }
              />
              <Route
                path="request-log"
                element={
                  <RoleRoute roles={['admin']}>
                    <RequestLogPage />
                  </RoleRoute>
                }
              />
              <Route
                path="evaluator-providers"
                element={
                  <RoleRoute roles={['admin']}>
                    <EvaluatorProvidersPage />
                  </RoleRoute>
                }
              />
              {/* 多租户开户管理（内部 admin 专属） */}
              <Route
                path="admin/tenants"
                element={
                  <RoleRoute roles={['admin']}>
                    <TenantsPage />
                  </RoleRoute>
                }
              />
              {/* 客户反馈回流展示（内部 admin 专属，superadmin 跨租户可见） */}
              <Route
                path="feedback"
                element={
                  <RoleRoute roles={['admin']}>
                    <FeedbackReviewPage />
                  </RoleRoute>
                }
              />
            </Route>
          </Routes>
        </BrowserRouter>
      </ConfirmProvider>
    </ToastProvider>
  )
}
