import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// SSE / 流式响应必须走原生 fetch：axios 的 XHR 适配器拿不到增量 body，
// 只能等整段响应结束，逐字打字机效果就没了。代价是 token 注入与 401 刷新
// 这两件拦截器代劳的事要在这里复刻一遍——刷新成功后原样重放一次。
//
// path 不带 /api 前缀（与 api 实例的 baseURL 对齐），body 必须是可重放的
// 字符串（流式 body 重放会失败，这里不支持）。
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const call = (token: string | null) => {
    const headers = new Headers(init.headers)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    return fetch(`/api${path}`, { ...init, headers })
  }

  const first = await call(useAuthStore.getState().accessToken)
  if (first.status !== 401) return first

  const refreshToken = useAuthStore.getState().refreshToken
  if (!refreshToken) {
    useAuthStore.getState().logout()
    return first
  }
  try {
    const res = await axios.post('/api/auth/refresh', { refresh_token: refreshToken })
    useAuthStore.getState().setTokens(res.data.access_token, res.data.refresh_token)
  } catch {
    useAuthStore.getState().logout()
    return first
  }
  return call(useAuthStore.getState().accessToken)
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true
      const refreshToken = useAuthStore.getState().refreshToken
      if (refreshToken) {
        try {
          const res = await axios.post('/api/auth/refresh', { refresh_token: refreshToken })
          const { access_token, refresh_token } = res.data
          useAuthStore.getState().setTokens(access_token, refresh_token)
          originalRequest.headers.Authorization = `Bearer ${access_token}`
          return api(originalRequest)
        } catch {
          useAuthStore.getState().logout()
        }
      } else {
        useAuthStore.getState().logout()
      }
    }
    return Promise.reject(error)
  },
)

export default api
