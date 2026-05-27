import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { authApi } from '@/services'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui'

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { setTokens, setUser } = useAuthStore()
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await authApi.login({ username, password })
      setTokens(res.data.access_token, res.data.refresh_token)
      const me = await authApi.getMe()
      setUser(me.data)
      navigate('/')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-6 py-10">
      <div className="w-full max-w-[360px]">
        <div className="card px-7 py-8">
          <header className="mb-7">
            <div className="page-eyebrow">Agent-Eval</div>
            <h1 className="text-title-2 font-display font-semibold text-text-primary mt-1">登录</h1>
            <p className="page-subtitle">智能体评测平台</p>
          </header>

          <form onSubmit={handleSubmit} noValidate className="space-y-4">
            <div>
              <label htmlFor="username" className="field-label">用户名</label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                placeholder="输入用户名"
                autoComplete="username"
                className="input"
              />
            </div>

            <div>
              <label htmlFor="password" className="field-label">密码</label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  placeholder="输入密码"
                  autoComplete="current-password"
                  className="input pr-14"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] tracking-[0.1em] uppercase text-text-tertiary hover:text-text-primary transition-colors"
                >
                  {showPassword ? '隐藏' : '显示'}
                </button>
              </div>
            </div>

            {error && (
              <p className="text-[12px] text-negative">{error}</p>
            )}

            <Button type="submit" variant="primary" size="lg" block loading={loading}>
              登录
            </Button>
          </form>

          <p className="mt-6 text-center text-[12px] text-text-tertiary">
            还没有账户？
            <Link to="/register" className="text-accent hover:text-accent-hover ml-1 no-underline transition-colors">
              创建账户
            </Link>
          </p>
        </div>

        <footer className="mt-6 text-center text-[10px] text-text-tertiary tracking-[0.1em] uppercase">
          &copy; 2026 Agent-Eval
        </footer>
      </div>
    </div>
  )
}
