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

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  return <>{children}</>
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
              <Route index element={<Navigate to="/dashboard" replace />} />
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
              <Route path="evaluator-providers" element={<EvaluatorProvidersPage />} />
              <Route path="auto-collect" element={<AutoCollectPage />} />
              <Route path="config" element={<ConfigPage />} />
              <Route path="audit" element={<AuditPage />} />
              <Route path="request-log" element={<RequestLogPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ConfirmProvider>
    </ToastProvider>
  )
}
