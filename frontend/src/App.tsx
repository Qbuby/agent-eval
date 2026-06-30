import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'
import { ConfirmProvider, ToastProvider } from '@/components/ui'
import Layout from '@/components/Layout'
import LoginPage from '@/pages/LoginPage'
import RegisterPage from '@/pages/RegisterPage'
import DashboardPage from '@/pages/DashboardPage'
import DatasetsPage from '@/pages/DatasetsPage'
import DatasetDetailPage from '@/pages/DatasetDetailPage'
import ConversationDatasetPage from '@/pages/ConversationDatasetPage'
import ConversationDatasetDetailPage from '@/pages/ConversationDatasetDetailPage'
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
import EntryCodesPage from '@/pages/admin/EntryCodesPage'
import FeedbackReviewPage from '@/pages/FeedbackReviewPage'
import LangfuseMetricsPage from '@/pages/LangfuseMetricsPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  return <>{children}</>
}

// 越权落地：external_customer 不该看到任何内部页，弹回 portal；内部角色弹回 dashboard。
function roleFallback(isExternal: boolean): string {
  return isExternal ? '/portal' : '/dashboard'
}

// Role-aware guard. Renders children only when the current user's role is in
// `roles`; otherwise redirects by role. Assumes it is nested under
// ProtectedRoute, so the user is already authenticated here.
function RoleRoute({ roles, children }: { roles: string[]; children: React.ReactNode }) {
  const role = useAuthStore((s) => s.role)
  const isExternal = useAuthStore((s) => s.isExternal)
  if (!roles.includes(role() ?? '')) return <Navigate to={roleFallback(isExternal())} replace />
  return <>{children}</>
}

// 内部功能门禁：仅 admin|user 可达；external_customer 越权改 URL 进来时弹回 portal。
// 嵌套在 ProtectedRoute 下，到此处已确保登录。
function InternalRoute({ children }: { children: React.ReactNode }) {
  const role = useAuthStore((s) => s.role)
  if (!['admin', 'user'].includes(role() ?? '')) return <Navigate to="/portal" replace />
  return <>{children}</>
}

// 入口反转的落地分流：登录后访问 "/" 时统一落地 dashboard。
// 各角色都有仪表盘（页面内按角色分流），外部客户仪表盘聚焦其评审进度。
function LandingRedirect() {
  return <Navigate to="/dashboard" replace />
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
              {/* 仪表盘：全角色可达（含 external_customer），页面内按角色分流渲染。 */}
              <Route path="dashboard" element={<DashboardPage />} />
              <Route path="datasets" element={<InternalRoute><DatasetsPage /></InternalRoute>} />
              <Route path="conversations" element={<InternalRoute><ConversationDatasetPage /></InternalRoute>} />
              <Route path="conversations/:name" element={<InternalRoute><ConversationDatasetDetailPage /></InternalRoute>} />
              <Route path="datasets/:name" element={<InternalRoute><DatasetDetailPage /></InternalRoute>} />
              <Route path="projects" element={<InternalRoute><ProjectsPage /></InternalRoute>} />
              <Route path="benchmark/:projectId" element={<InternalRoute><BenchmarkPage /></InternalRoute>} />
              <Route path="generate" element={<InternalRoute><GeneratePage /></InternalRoute>} />
              <Route path="traces" element={<InternalRoute><TracesPage /></InternalRoute>} />
              <Route path="evaluation" element={<InternalRoute><EvaluationPage /></InternalRoute>} />
              <Route path="evaluation/compare" element={<InternalRoute><EvaluationComparePage /></InternalRoute>} />
              <Route path="evaluation/runs/:runId" element={<InternalRoute><EvaluationRunDetailPage /></InternalRoute>} />
              <Route path="evaluators" element={<InternalRoute><EvaluatorsPage /></InternalRoute>} />
              <Route path="evaluators/compare" element={<InternalRoute><EvaluatorComparePage /></InternalRoute>} />
              {/* 自动采集（内部 admin 专属，内部普通 user 不需要） */}
              <Route
                path="auto-collect"
                element={
                  <RoleRoute roles={['admin']}>
                    <AutoCollectPage />
                  </RoleRoute>
                }
              />
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
              {/* 注册入口码管理（内部 admin 专属） */}
              <Route
                path="admin/entry-codes"
                element={
                  <RoleRoute roles={['admin']}>
                    <EntryCodesPage />
                  </RoleRoute>
                }
              />
              {/* 客户反馈回流展示（内部角色可见：admin + 内部普通 user；
                  后端 feedback router 对内部 user 跨租户旁路，故能看到全部反馈） */}
              <Route
                path="feedback"
                element={
                  <InternalRoute>
                    <FeedbackReviewPage />
                  </InternalRoute>
                }
              />
              {/* Langfuse Tracing 指标（内部角色可见：admin + 内部普通 user） */}
              <Route
                path="tracing-metrics"
                element={
                  <InternalRoute>
                    <LangfuseMetricsPage />
                  </InternalRoute>
                }
              />
            </Route>
          </Routes>
        </BrowserRouter>
      </ConfirmProvider>
    </ToastProvider>
  )
}
