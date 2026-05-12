import api from './client'
import type {
  LoginRequest,
  RegisterRequest,
  TokenResponse,
  User,
  UserUpdateRequest,
} from '@/types'

export const authApi = {
  login(data: LoginRequest) {
    return api.post<TokenResponse>('/auth/login', data)
  },
  register(data: RegisterRequest) {
    return api.post<User>('/auth/register', data)
  },
  refresh(refreshToken: string) {
    return api.post<TokenResponse>('/auth/refresh', { refresh_token: refreshToken })
  },
  getMe() {
    return api.get<User>('/auth/me')
  },
  updateMe(data: UserUpdateRequest) {
    return api.put<User>('/auth/me', data)
  },
}
