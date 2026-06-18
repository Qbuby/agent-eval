import MarkdownView from './MarkdownView'
import type { TestCase, TurnExpectation } from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// 多轮对话样例的只读展示：消息按 role 渲染成左右气泡（user 右、assistant/其余
// 左），内容走 MarkdownView。可选地在每条 user 消息下方挂出该轮的逐轮期望
// （turn_expectations 按 turn_index 对齐到 input_messages 下标），并在顶部展示
// 会话级目标 conversation_goal。
// 单轮老数据（仅一条 user 消息、无 goal/turn_expectations）也能正常渲染。
// ──────────────────────────────────────────────────────────────────────────

const ROLE_LABEL: Record<string, string> = {
  user: '用户',
  assistant: '助手',
  system: '系统',
  tool: '工具',
}

function TurnExpectationBlock({ exp }: { exp: TurnExpectation }) {
  const hasCriteria = exp.criteria && exp.criteria.length > 0
  if (!hasCriteria && !exp.expected_output) return null
  return (
    <div className="mt-1.5 ml-2 border-l-2 border-accent/40 pl-2.5 text-[11px] space-y-1">
      {exp.expected_output && (
        <div>
          <span className="text-text-tertiary">期望输出：</span>
          <span className="text-text-secondary">{exp.expected_output}</span>
        </div>
      )}
      {hasCriteria && (
        <div>
          <span className="text-text-tertiary">评判要点：</span>
          <ul className="list-disc list-inside text-text-secondary">
            {exp.criteria!.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function ConversationView({ testCase }: { testCase: TestCase }) {
  const messages = testCase.input_messages ?? []
  // turn_index → 该轮期望，便于按消息下标对齐。
  const expByIndex = new Map<number, TurnExpectation>()
  for (const te of testCase.turn_expectations ?? []) {
    expByIndex.set(te.turn_index, te)
  }

  return (
    <div className="space-y-3">
      {testCase.conversation_goal && (
        <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-[12px]">
          <span className="font-medium text-accent">会话目标</span>
          <div className="mt-1 text-text-secondary">
            <MarkdownView text={testCase.conversation_goal} />
          </div>
        </div>
      )}

      {messages.map((m, i) => {
        const isUser = m.role === 'user'
        const exp = expByIndex.get(i)
        return (
          <div key={i} className={isUser ? 'flex flex-col items-end' : 'flex flex-col items-start'}>
            <div className={`max-w-[85%] rounded-lg px-3 py-2 ${
              isUser ? 'bg-accent/10 border border-accent/20' : 'bg-fill/5 border border-border'
            }`}>
              <div className="text-[10px] uppercase tracking-wide text-text-tertiary mb-1">
                {ROLE_LABEL[m.role] || m.role}
              </div>
              <div className="text-[12px] text-text-primary">
                <MarkdownView text={m.content} />
              </div>
            </div>
            {isUser && exp && (
              <div className="max-w-[85%] w-full">
                <TurnExpectationBlock exp={exp} />
              </div>
            )}
          </div>
        )
      })}

      {messages.length === 0 && (
        <div className="empty-state text-[12px]">该样例无对话消息</div>
      )}
    </div>
  )
}
