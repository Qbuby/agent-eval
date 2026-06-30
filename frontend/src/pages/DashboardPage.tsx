import { useAuthStore } from '@/stores/auth'
import InternalDashboard from './dashboard/InternalDashboard'
import ExternalDashboard from './dashboard/ExternalDashboard'

// 仪表盘按角色分流：external_customer → 评审进度视图（仅本租户 portal 数据），
// 内部角色（admin + 普通 user）→ 运营总览（数据资产 / Tracing 趋势 / 客户反馈）。
// 子页各自只调本角色有权访问的接口，避免 403。
export default function DashboardPage() {
  const isExternal = useAuthStore((s) => s.isExternal)
  return isExternal() ? <ExternalDashboard /> : <InternalDashboard />
}
