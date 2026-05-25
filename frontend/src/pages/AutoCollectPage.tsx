import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useConfirm } from '@/components/ui'
import { schedulerApi, routingApi } from '@/services'
import type { RoutingRule } from '@/types'

export default function AutoCollectPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
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
      <header className="mb-8">
        <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary">自动化</div>
        <h1 className="text-xl font-medium tracking-tight">自动采集</h1>
        <p className="text-[12px] text-text-tertiary mt-0.5">定时轮询 LangSmith 项目，按路由规则自动导入样例</p>
      </header>

      {/* 全局状态卡片 */}
      <div className="grid grid-cols-4 gap-3 mb-8">
        <div className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">调度器</div>
          <div className={`text-lg font-medium ${status?.running ? 'text-positive' : 'text-negative'}`}>
            {status?.running ? '运行中' : '已停止'}
          </div>
        </div>
        <div className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">监听项目</div>
          <div className="text-lg font-medium">{activeWatches} / {totalWatches}</div>
        </div>
        <div className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">路由规则</div>
          <div className="text-lg font-medium">{rules?.filter(r => r.is_active).length ?? 0} 条生效</div>
        </div>
        <div className="p-4 border border-border rounded-lg bg-surface hover:-translate-y-0.5 hover:shadow-sm transition-all">
          <div className="text-[10px] tracking-widest uppercase text-text-tertiary mb-1">累计路由</div>
          <div className="text-lg font-medium">{stats?.reduce((sum, s) => sum + s.routed, 0) ?? 0}</div>
        </div>
      </div>

      {/* 监听项目 */}
      <div className="flex items-center justify-between mb-4 pb-2 border-b border-border">
        <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary">监听项目</div>
        <button
          onClick={() => setShowAddWatch(true)}
          className="py-1.5 px-3 text-[10px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] transition-all"
        >
          + 添加监听
        </button>
      </div>

      <div className="border border-border rounded-[6px] overflow-hidden bg-surface mb-8">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">项目名称</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-20">状态</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-36">上次轮询</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-right py-2 px-3 border-b border-border font-normal bg-accent-subtle w-40">操作</th>
            </tr>
          </thead>
          <tbody>
            {status?.watches?.map(w => (
              <tr key={w.project_name} className="hover:bg-accent-subtle group">
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-primary font-medium">{w.project_name}</td>
                <td className="py-2.5 px-3 border-b border-border">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium ${w.status === 'active' ? 'bg-[#e6f7ed] text-[#1a6]' : 'bg-[#f5f5f5] text-[#999]'}`}>
                    {w.status === 'active' ? '运行' : '暂停'}
                  </span>
                </td>
                <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary">
                  {w.last_poll ? new Date(w.last_poll).toLocaleString() : '—'}
                </td>
                <td className="py-2.5 px-3 border-b border-border text-right">
                  <div className="flex gap-2 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => triggerMutation.mutate(w.project_name)}
                      className="text-[10px] text-text-secondary hover:text-accent"
                    >立即执行</button>
                    {w.status === 'active' ? (
                      <button onClick={() => pauseWatchMutation.mutate(w.project_name)} className="text-[10px] text-text-secondary hover:text-warning">暂停</button>
                    ) : (
                      <button onClick={() => resumeWatchMutation.mutate(w.project_name)} className="text-[10px] text-text-secondary hover:text-positive">恢复</button>
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
                      className="text-[10px] text-text-secondary hover:text-negative"
                    >移除</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(!status?.watches || status.watches.length === 0) && (
          <div className="text-center py-8 text-text-tertiary text-[12px]">暂无监听项目</div>
        )}
      </div>

      {/* 路由规则 */}
      <div className="flex items-center justify-between mb-4 pb-2 border-b border-border">
        <div className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary">路由规则</div>
        <button
          onClick={() => setShowAddRule(true)}
          className="py-1.5 px-3 text-[10px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 active:scale-[0.97] transition-all"
        >
          + 新建规则
        </button>
      </div>

      <div className="border border-border rounded-[6px] overflow-hidden bg-surface">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">规则名称</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">来源项目</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle">目标数据集</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-16">优先级</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-left py-2 px-3 border-b border-border font-normal bg-accent-subtle w-16">状态</th>
              <th className="text-[9px] tracking-[0.1em] uppercase text-text-tertiary text-right py-2 px-3 border-b border-border font-normal bg-accent-subtle w-24">操作</th>
            </tr>
          </thead>
          <tbody>
            {rules?.map((rule: RoutingRule) => (
              <tr key={rule.id} className="hover:bg-accent-subtle group">
                <td className="py-2.5 px-3 border-b border-border text-[12px] text-text-primary font-medium">{rule.name}</td>
                <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-secondary">{rule.source_project}</td>
                <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-secondary">{rule.target_dataset}</td>
                <td className="py-2.5 px-3 border-b border-border text-[11px] text-text-tertiary">{rule.priority}</td>
                <td className="py-2.5 px-3 border-b border-border">
                  <button
                    onClick={() => toggleRuleMutation.mutate({ id: rule.id, active: !rule.is_active })}
                    className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-medium cursor-pointer transition-all ${rule.is_active ? 'bg-[#e6f7ed] text-[#1a6]' : 'bg-[#f5f5f5] text-[#999]'}`}
                  >
                    {rule.is_active ? '启用' : '禁用'}
                  </button>
                </td>
                <td className="py-2.5 px-3 border-b border-border text-right">
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
                    className="text-[10px] text-text-secondary hover:text-negative opacity-0 group-hover:opacity-100 transition-all"
                  >删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules?.length === 0 && (
          <div className="text-center py-8 text-text-tertiary text-[12px]">暂无路由规则</div>
        )}
      </div>

      {/* 添加监听弹窗 */}
      {showAddWatch && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowAddWatch(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[380px] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">添加监听项目</h2>
              <button onClick={() => setShowAddWatch(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">LangSmith 项目名称</label>
                <input
                  value={watchProject}
                  onChange={e => setWatchProject(e.target.value)}
                  placeholder="例如：ruyi-agent"
                  className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all"
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowAddWatch(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => addWatchMutation.mutate()}
                  disabled={!watchProject.trim() || addWatchMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
                >添加</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 新建规则弹窗 */}
      {showAddRule && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowAddRule(false)}>
          <div className="bg-surface border border-border rounded-lg p-6 w-[440px] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-[14px] font-medium">新建路由规则</h2>
              <button onClick={() => setShowAddRule(false)} className="text-text-tertiary hover:text-text-primary text-lg">×</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">规则名称</label>
                <input value={ruleName} onChange={e => setRuleName(e.target.value)} placeholder="例如：生产环境调用轨迹" className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all" />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">来源项目</label>
                <input value={ruleSource} onChange={e => setRuleSource(e.target.value)} placeholder="LangSmith 项目名" className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all" />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">目标数据集</label>
                <input value={ruleTarget} onChange={e => setRuleTarget(e.target.value)} placeholder="目标数据集名称" className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all" />
              </div>
              <div>
                <label className="block text-[10px] tracking-widest uppercase text-text-tertiary mb-1.5">优先级（数字越小越优先）</label>
                <input value={rulePriority} onChange={e => setRulePriority(e.target.value)} type="number" className="w-full py-2 px-2.5 text-[12px] border border-border rounded-[6px] bg-surface outline-none focus:border-accent transition-all" />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button onClick={() => setShowAddRule(false)} className="py-2 px-3.5 text-[11px] rounded-[6px] border border-border hover:bg-accent-subtle transition-all">取消</button>
                <button
                  onClick={() => createRuleMutation.mutate()}
                  disabled={!ruleName.trim() || !ruleSource.trim() || !ruleTarget.trim() || createRuleMutation.isPending}
                  className="py-2 px-3.5 text-[11px] font-medium rounded-[6px] bg-accent text-white border border-accent hover:opacity-90 disabled:opacity-40 transition-all"
                >创建</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
