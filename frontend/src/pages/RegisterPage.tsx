import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { authApi } from '@/services'
import { Button } from '@/components/ui'
import { formatApiError, toToastMessage } from '@/lib/errors'

export default function RegisterPage() {
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [entryCode, setEntryCode] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    setError('')
    setLoading(true)
    try {
      await authApi.register({ username, email, password, entry_code: entryCode || undefined })
      navigate('/login')
    } catch (err: unknown) {
      const norm = formatApiError(err, { fallbackTitle: '注册失败', fallbackMessage: '注册失败' })
      setError(toToastMessage(norm))
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
            <h1 className="text-title-2 font-display font-semibold text-text-primary mt-1">创建账户</h1>
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
                minLength={3}
                placeholder="选择用户名"
                autoComplete="username"
                className="input"
              />
            </div>

            <div>
              <label htmlFor="email" className="field-label">邮箱</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="you@example.com"
                autoComplete="email"
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
                  minLength={8}
                  placeholder="至少 8 位，含字母和数字"
                  autoComplete="new-password"
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

            <div>
              <label htmlFor="confirm-password" className="field-label">确认密码</label>
              <input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                placeholder="再次输入密码"
                autoComplete="new-password"
                className="input"
              />
            </div>

            <div>
              <label htmlFor="entry-code" className="field-label">入口码</label>
              <input
                id="entry-code"
                type="text"
                value={entryCode}
                onChange={(e) => setEntryCode(e.target.value)}
                placeholder="由管理员提供的注册入口码"
                autoComplete="off"
                className="input"
              />
            </div>

            {error && (
              <p className="text-[12px] text-negative">{error}</p>
            )}

            <Button type="submit" variant="primary" size="lg" block loading={loading}>
              创建账户
            </Button>
          </form>

          <p className="mt-6 text-center text-[12px] text-text-tertiary">
            已有账户？
            <Link to="/login" className="text-accent hover:text-accent-hover ml-1 no-underline transition-colors">
              登录
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
