import { useState } from 'react'
import { Button } from './ui'
import type { TestCase, TurnExpectation } from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// 多轮对话样例编辑器：受控组件，对外通过 value/onChange 暴露一个 TestCase。
// 支持：
//   - 增删/编辑每条消息（role 下拉 + content 文本域）
//   - 会话级目标 conversation_goal
//   - 逐轮期望：仅对 user 消息开放，turn_index 锁定为该消息在 input_messages
//     里的下标，避免手填错位。
// 父组件负责 name/description/提交，本组件只管对话主体 + 期望。
// ──────────────────────────────────────────────────────────────────────────

const ROLES = ['user', 'assistant', 'system', 'tool'] as const

type Msg = { role: string; content: string }

export default function ConversationEditor({
  value,
  onChange,
}: {
  value: TestCase
  onChange: (next: TestCase) => void
}) {
  const messages = value.input_messages ?? []
  const expByIndex = new Map<number, TurnExpectation>()
  for (const te of value.turn_expectations ?? []) expByIndex.set(te.turn_index, te)

  const [expandedExp, setExpandedExp] = useState<Set<number>>(new Set())

  function patch(p: Partial<TestCase>) {
    onChange({ ...value, ...p })
  }

  function setMessages(next: Msg[]) {
    patch({ input_messages: next })
  }

  function updateMsg(i: number, m: Partial<Msg>) {
    setMessages(messages.map((x, idx) => (idx === i ? { ...x, ...m } : x)))
  }

  function addMsg() {
    // 默认交替角色：上一条是 user 则补 assistant，否则补 user。
    const last = messages[messages.length - 1]
    const role = last?.role === 'user' ? 'assistant' : 'user'
    setMessages([...messages, { role, content: '' }])
  }

  function removeMsg(i: number) {
    // 删消息会让后续 turn_index 错位，故同步重映射 turn_expectations。
    const nextMsgs = messages.filter((_, idx) => idx !== i)
    const nextExps = (value.turn_expectations ?? [])
      .filter(te => te.turn_index !== i)
      .map(te => (te.turn_index > i ? { ...te, turn_index: te.turn_index - 1 } : te))
    onChange({ ...value, input_messages: nextMsgs, turn_expectations: nextExps })
  }

  function setExp(i: number, p: Partial<TurnExpectation>) {
    const existing = expByIndex.get(i)
    const merged: TurnExpectation = { turn_index: i, ...existing, ...p }
    const others = (value.turn_expectations ?? []).filter(te => te.turn_index !== i)
    // 期望全空则移除，避免存一堆空壳。
    const isEmpty = (!merged.criteria || merged.criteria.length === 0) && !merged.expected_output
    patch({ turn_expectations: isEmpty ? others : [...others, merged].sort((a, b) => a.turn_index - b.turn_index) })
  }

  function toggleExp(i: number) {
    setExpandedExp(prev => {
      const n = new Set(prev)
      if (n.has(i)) n.delete(i); else n.add(i)
      return n
    })
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="field-label">会话目标（可选，整段对话的总体期望）</label>
        <textarea
          value={value.conversation_goal ?? ''}
          onChange={e => patch({ conversation_goal: e.target.value || undefined })}
          rows={2}
          placeholder="如：用户想退货，助手应在确认订单后给出退货流程并安抚情绪"
          className="input resize-y"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="field-label mb-0">对话消息</label>
          <Button variant="secondary" size="sm" onClick={addMsg}>+ 添加消息</Button>
        </div>
        <div className="space-y-2">
          {messages.map((m, i) => {
            const isUser = m.role === 'user'
            const exp = expByIndex.get(i)
            const expanded = expandedExp.has(i)
            return (
              <div key={i} className="border border-border rounded-md p-2.5 bg-fill/5">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-[11px] text-text-tertiary w-6">#{i}</span>
                  <select
                    value={m.role}
                    onChange={e => updateMsg(i, { role: e.target.value })}
                    className="select-sm"
                  >
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                  <div className="flex-1" />
                  {isUser && (
                    <button
                      type="button"
                      onClick={() => toggleExp(i)}
                      className={`text-[11px] ${exp ? 'text-accent' : 'text-text-tertiary'} hover:text-text-primary`}
                    >
                      {exp ? '本轮期望 ●' : '+ 本轮期望'}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => removeMsg(i)}
                    className="text-[11px] text-action-danger"
                  >
                    删除
                  </button>
                </div>
                <textarea
                  value={m.content}
                  onChange={e => updateMsg(i, { content: e.target.value })}
                  rows={2}
                  placeholder="消息内容（支持 Markdown）"
                  className="input resize-y text-[12px]"
                />
                {isUser && (expanded || exp) && (
                  <div className="mt-2 ml-2 border-l-2 border-accent/40 pl-2.5 space-y-2">
                    <div>
                      <label className="field-label text-[11px]">期望输出（可选）</label>
                      <textarea
                        value={exp?.expected_output ?? ''}
                        onChange={e => setExp(i, { expected_output: e.target.value || undefined })}
                        rows={2}
                        placeholder="这一轮助手理想的回复"
                        className="input resize-y text-[12px]"
                      />
                    </div>
                    <div>
                      <label className="field-label text-[11px]">评判要点（逗号分隔，可选）</label>
                      <input
                        value={(exp?.criteria ?? []).join(', ')}
                        onChange={e => setExp(i, {
                          criteria: e.target.value
                            ? e.target.value.split(',').map(s => s.trim()).filter(Boolean)
                            : [],
                        })}
                        placeholder="要点1, 要点2"
                        className="input text-[12px]"
                      />
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {messages.length === 0 && (
            <div className="empty-state text-[12px]">还没有消息，点击「添加消息」开始构建对话</div>
          )}
        </div>
      </div>
    </div>
  )
}
