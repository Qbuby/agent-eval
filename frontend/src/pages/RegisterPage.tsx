import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { authApi } from '@/services'

export default function RegisterPage() {
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
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
      await authApi.register({ username, email, password })
      navigate('/login')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || '注册失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-white px-6 py-10">
      <div className="w-full max-w-[340px] animate-fade-in">
        <header className="mb-10 text-left">
          <div className="text-base font-medium tracking-tight text-text-primary mb-0.5">Agent-Eval</div>
          <div className="text-[11px] font-light text-text-tertiary tracking-wide">智能体评测平台</div>
        </header>

        <h2 className="text-[13px] font-medium text-text-primary mb-7 tracking-wide">创建账户</h2>

        <form onSubmit={handleSubmit} noValidate>
          <div className="mb-5 relative group">
            <label htmlFor="username" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              用户名
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              placeholder="选择用户名"
              autoComplete="username"
              className="w-full py-2 px-0 bg-transparent border-0 border-b border-border text-[14px] font-light text-text-primary placeholder:text-border focus:border-accent focus:outline-none transition-all duration-200"
            />
          </div>

          <div className="mb-5 relative group">
            <label htmlFor="email" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              邮箱
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="you@example.com"
              autoComplete="email"
              className="w-full py-2 px-0 bg-transparent border-0 border-b border-border text-[14px] font-light text-text-primary placeholder:text-border focus:border-accent focus:outline-none transition-all duration-200"
            />
          </div>

          <div className="mb-5 relative group">
            <label htmlFor="password" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              密码
            </label>
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
                className="w-full py-2 px-0 bg-transparent border-0 border-b border-border text-[14px] font-light text-text-primary placeholder:text-border focus:border-accent focus:outline-none transition-all duration-200"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-0 top-1/2 -translate-y-1/2 text-[10px] tracking-widest uppercase text-text-tertiary hover:text-text-primary active:scale-95 transition-all"
              >
                {showPassword ? '隐藏' : '显示'}
              </button>
            </div>
          </div>

          <div className="mb-7 relative group">
            <label htmlFor="confirm-password" className="block text-[10px] text-text-tertiary tracking-widest uppercase mb-1.5 group-focus-within:text-accent transition-colors">
              确认密码
            </label>
            <input
              id="confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              placeholder="再次输入密码"
              autoComplete="new-password"
              className="w-full py-2 px-0 bg-transparent border-0 border-b border-border text-[14px] font-light text-text-primary placeholder:text-border focus:border-accent focus:outline-none transition-all duration-200"
            />
          </div>

          {error && (
            <p className="text-[11px] text-negative mb-3 animate-fade-in">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-accent text-white border-none rounded-[3px] text-[13px] font-normal tracking-wide cursor-pointer hover:opacity-90 hover:scale-[1.01] active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:scale-100 transition-all duration-200"
          >
            {loading ? (
              <span className="inline-block w-3 h-3 border border-white/40 border-t-white rounded-full animate-spin" />
            ) : '创建账户'}
          </button>
        </form>

        <p className="mt-8 text-center text-[11px] text-text-tertiary">
          已有账户？
          <Link to="/login" className="text-text-primary no-underline font-normal ml-1 hover:opacity-60 transition-opacity">
            登录
          </Link>
        </p>

        <footer className="mt-14 text-center text-[10px] text-border tracking-wide">
          &copy; 2026 Agent-Eval
        </footer>
      </div>
    </div>
  )
}
