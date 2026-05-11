import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'
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

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated()) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
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
          <Route path="auto-collect" element={<AutoCollectPage />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="audit" element={<AuditPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
