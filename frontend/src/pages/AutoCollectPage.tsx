import { useId, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Dialog, useConfirm } from '@/components/ui'
import { schedulerApi, routingApi } from '@/services'
import type { RoutingRule } from '@/types'

export default function AutoCollectPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const reactId = useId()
  const watchProjectId = `${reactId}-watch-project`
  const ruleNameId = `${reactId}-rule-name`
  const ruleSourceId = `${reactId}-rule-source`
  const ruleTargetId = `${reactId}-rule-target`
  const rulePriorityId = `${reactId}-rule-priority`
  const [showAddRule, setShowAddRule] = useState(false)
  const [showAddWatch, setShowAddWatch] = useState(false)
  const [watchProject, setWatchProject] = useState('')
  const [ruleName, setRuleName] = useState('')
  const [ruleSource, setRuleSource] = useState('')
  const [ruleTarget, setRuleTarget] = useState('')
  const [rulePriority, setRulePriority] = useState('10')

  const { data: status } = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: () => schedulerApi.getStatus().then(r => r.data),
    refetchInterval: 10000,
  })

  const { data: rules } = useQuery({
    queryKey: ['routing-rules'],
    queryFn: () => routingApi.listRules().then(r => r.data),
  })

  const { data: stats } = useQuery({
    queryKey: ['routing-stats'],
    queryFn: () => routingApi.getStats().then(r => r.data),
  })

  const addWatchMutation = useMutation({
    mutationFn: () => schedulerApi.addWatch(watchProject),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scheduler-status'] })
      setShowAddWatch(false)
      setWatchProject('')
    },
  })

  const removeWatchMutation = useMutation({
    mutationFn: (name: string) => schedulerApi.removeWatch(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['scheduler-status'] }),
  })

  const pauseWatchMutation = useMutation({
    mutationFn: (name: string) => schedulerApi.pauseWatch(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['scheduler-status'] }),
  })

  const resumeWatchMutation = useMutation({
    mutationFn: (name: string) => schedulerApi.resumeWatch(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['scheduler-status'] }),
  })

  const triggerMutation = useMutation({
    mutationFn: (name: string) => schedulerApi.triggerPoll(name),
  })

  const createRuleMutation = useMutation({
    mutationFn: () => routingApi.createRule({
      name: ruleName,
      source_project: ruleSource,
      target_dataset: ruleTarget,
      priority: parseInt(rulePriority) || 10,
      is_active: true,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['routing-rules'] })
      setShowAddRule(false)
      setRuleName('')
      setRuleSource('')
      setRuleTarget('')
      setRulePriority('10')
    },
  })

  const toggleRuleMutation = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => routingApi.updateRule(id, { is_active: active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['routing-rules'] }),
  })

  const deleteRuleMutation = useMutation({
    mutationFn: (id: string) => routingApi.deleteRule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['routing-rules'] }),
  })

  const activeWatches = status?.watches?.filter(w => w.status === 'active').length ?? 0
  const totalWatches = status?.watches?.length ?? 0

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">自动化</div>
        <h1 className="page-title">自动采集</h1>
        <p className="page-subtitle">定时轮询 LangSmith 项目，按路由规则自动导入样例</p>
      </header>

      <div className="grid grid-cols-4 gap-3 mb-8">
        <div className="metric-card">
          <div className="metric-eyebrow">调度器</div>
          <div className={`metric-value ${status?.running ? 'text-positive' : 'text-negative'}`}>
            {status?.running ? '运行中' : '已停止'}
          </div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">监听项目</div>
          <div className="metric-value">{activeWatches} / {totalWatches}</div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">路由规则</div>
          <div className="metric-value">{rules?.filter(r => r.is_active).length ?? 0}<span className="text-[12px] font-normal text-text-tertiary ml-1">条生效</span></div>
        </div>
        <div className="metric-card">
          <div className="metric-eyebrow">累计路由</div>
          <div className="metric-value">{stats?.reduce((sum, s) => sum + s.routed, 0) ?? 0}</div>
        </div>
      </div>

      <div className="section-row">
        <div className="page-eyebrow">监听项目</div>
        <Button onClick={() => setShowAddWatch(true)} variant="primary" size="sm">添加监听</Button>
      </div>

      <div className="table-card mb-8">
        <table className="table-base">
          <thead>
            <tr>
              <th>项目名称</th>
              <th className="w-24">状态</th>
              <th className="w-44">上次轮询</th>
              <th className="w-44 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {status?.watches?.map(w => (
              <tr key={w.project_name} className="group">
                <td className="font-medium">{w.project_name}</td>
                <td>
                  <span className={w.status === 'active' ? 'badge badge-positive' : 'badge badge-neutral'}>
                    {w.status === 'active' ? '运行' : '暂停'}
                  </span>
                </td>
                <td className="text-text-tertiary text-[11px]">
                  {w.last_poll ? new Date(w.last_poll).toLocaleString() : '—'}
                </td>
                <td className="text-right">
                  <div className="flex gap-3 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => triggerMutation.mutate(w.project_name)}
                      className="text-action"
                    >立即执行</button>
                    {w.status === 'active' ? (
                      <button onClick={() => pauseWatchMutation.mutate(w.project_name)} className="text-action-warning">暂停</button>
                    ) : (
                      <button onClick={() => resumeWatchMutation.mutate(w.project_name)} className="text-action-positive">恢复</button>
                    )}
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: '移除监听',
                          description: `移除对 "${w.project_name}" 的监听？`,
                          confirmText: '移除',
                          danger: true,
                        })
                        if (ok) removeWatchMutation.mutate(w.project_name)
                      }}
                      className="text-action-danger"
                    >移除</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(!status?.watches || status.watches.length === 0) && (
          <div className="empty-state">暂无监听项目</div>
        )}
      </div>

      <div className="section-row">
        <div className="page-eyebrow">路由规则</div>
        <Button onClick={() => setShowAddRule(true)} variant="primary" size="sm">新建规则</Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th>规则名称</th>
              <th>来源项目</th>
              <th>目标数据集</th>
              <th className="w-20 text-right">优先级</th>
              <th className="w-20">状态</th>
              <th className="w-24 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {rules?.map((rule: RoutingRule) => (
              <tr key={rule.id} className="group">
                <td className="font-medium">{rule.name}</td>
                <td className="text-text-secondary">{rule.source_project}</td>
                <td className="text-text-secondary">{rule.target_dataset}</td>
                <td className="text-right text-text-tertiary tabular-nums">{rule.priority}</td>
                <td>
                  <button
                    onClick={() => toggleRuleMutation.mutate({ id: rule.id, active: !rule.is_active })}
                    className={`${rule.is_active ? 'badge badge-positive' : 'badge badge-neutral'} cursor-pointer transition-colors`}
                  >
                    {rule.is_active ? '启用' : '禁用'}
                  </button>
                </td>
                <td className="text-right">
                  <button
                    onClick={async () => {
                      const ok = await confirm({
                        title: '删除规则',
                        description: `删除规则 "${rule.name}"？`,
                        confirmText: '删除',
                        danger: true,
                      })
                      if (ok) deleteRuleMutation.mutate(rule.id)
                    }}
                    className="text-[11px] text-text-secondary hover:text-negative opacity-0 group-hover:opacity-100 transition-opacity"
                  >删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules?.length === 0 && <div className="empty-state">暂无路由规则</div>}
      </div>

      <Dialog
        open={showAddWatch}
        onClose={() => setShowAddWatch(false)}
        title="添加监听项目"
        width={400}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowAddWatch(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              onClick={() => addWatchMutation.mutate()}
              disabled={!watchProject.trim()}
              loading={addWatchMutation.isPending}
            >添加</Button>
          </>
        }
      >
        <div>
          <label htmlFor={watchProjectId} className="field-label">LangSmith 项目名称</label>
          <input
            id={watchProjectId}
            value={watchProject}
            onChange={e => setWatchProject(e.target.value)}
            placeholder="例如：ruyi-agent"
            className="input"
          />
        </div>
      </Dialog>

      <Dialog
        open={showAddRule}
        onClose={() => setShowAddRule(false)}
        title="新建路由规则"
        width={460}
        footer={
          <>
            <Button variant="secondary" size="md" onClick={() => setShowAddRule(false)}>取消</Button>
            <Button
              variant="primary"
              size="md"
              onClick={() => createRuleMutation.mutate()}
              disabled={!ruleName.trim() || !ruleSource.trim() || !ruleTarget.trim()}
              loading={createRuleMutation.isPending}
            >创建</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label htmlFor={ruleNameId} className="field-label">规则名称</label>
            <input id={ruleNameId} value={ruleName} onChange={e => setRuleName(e.target.value)} placeholder="例如：生产环境调用轨迹" className="input" />
          </div>
          <div>
            <label htmlFor={ruleSourceId} className="field-label">来源项目</label>
            <input id={ruleSourceId} value={ruleSource} onChange={e => setRuleSource(e.target.value)} placeholder="LangSmith 项目名" className="input" />
          </div>
          <div>
            <label htmlFor={ruleTargetId} className="field-label">目标数据集</label>
            <input id={ruleTargetId} value={ruleTarget} onChange={e => setRuleTarget(e.target.value)} placeholder="目标数据集名称" className="input" />
          </div>
          <div>
            <label htmlFor={rulePriorityId} className="field-label">优先级（数字越小越优先）</label>
            <input id={rulePriorityId} value={rulePriority} onChange={e => setRulePriority(e.target.value)} type="number" className="input" />
          </div>
        </div>
      </Dialog>
    </div>
  )
}
