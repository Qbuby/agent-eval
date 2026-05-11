import api from './client'
import type { SchedulerStatus } from '@/types'

export const schedulerApi = {
  getStatus() {
    return api.get<SchedulerStatus>('/scheduler/status')
  },
  addWatch(projectName: string) {
    return api.post('/scheduler/watch', { project_name: projectName })
  },
  removeWatch(projectName: string) {
    return api.delete(`/scheduler/watch/${projectName}`)
  },
  pauseWatch(projectName: string) {
    return api.post(`/scheduler/watch/${projectName}/pause`)
  },
  resumeWatch(projectName: string) {
    return api.post(`/scheduler/watch/${projectName}/resume`)
  },
  triggerPoll(projectName: string) {
    return api.post(`/scheduler/trigger/${projectName}`)
  },
}
