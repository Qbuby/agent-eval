// 任务 #115 UI 验收：备选数据集列表(/datasets)与多轮对话集列表(/conversations)。
// 断言点（每页）：
//   1. 页面加载后无 500/401 网络响应
//   2. 无 console error
//   3. 列表主体渲染出来（表格或空态，而非崩溃/长时间转圈）
//   4. 记录从导航到列表可见的耗时
// 用纯 \u 转义写中文，避开本机编码坑。
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = 'http://localhost'
const AUTH = new URL('./auth.json', import.meta.url)

const results = []
const ok = (n, c, extra = '') => results.push([c ? 'PASS' : 'FAIL', n, extra])

const browser = await chromium.launch({ headless: false })
const ctx = await browser.newContext({
  storageState: JSON.parse(fs.readFileSync(AUTH, 'utf8')),
  viewport: { width: 1600, height: 1000 },
})

async function probe(label, pathname, apiType) {
  const page = await ctx.newPage()
  const errors = []
  const badResp = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('response', (r) => {
    const s = r.status()
    if (s === 401 || s === 403 || s >= 500) badResp.push(`${s} ${r.url()}`)
  })

  const t0 = Date.now()
  await page.goto(`${BASE}${pathname}`, { waitUntil: 'domcontentloaded' })
  // 真实 DOM 是 Tailwind 自定义：数据卡片 div.card.cursor-pointer，空态 .empty-state
  // （加载骨架是 .card + .skeleton，不带 cursor-pointer，所以不会误判为已加载完）
  let visible = false
  try {
    await page.waitForSelector('div.card.cursor-pointer, .empty-state', { timeout: 12000 })
    visible = true
  } catch { visible = false }
  const dt = Date.now() - t0

  // 等网络稳定，收集迟到的错误响应
  try { await page.waitForLoadState('networkidle', { timeout: 5000 }) } catch {}

  ok(`${label} :: 列表可见`, visible, `${dt}ms`)
  ok(`${label} :: 无 5xx/401/403`, badResp.length === 0, badResp.slice(0, 3).join(' | '))
  ok(`${label} :: 无 console error`, errors.length === 0, errors.slice(0, 2).join(' | '))

  await page.screenshot({ path: `probe-115-${apiType}.png` })
  await page.close()
}

await probe('候选集', '/datasets', 'candidate')
await probe('多轮集', '/conversations', 'conversation')

await browser.close()

console.log('\n=== #115 UI 验收 ===')
for (const [v, n, e] of results) console.log(`  [${v}] ${n}${e ? '  -> ' + e : ''}`)
const failed = results.filter((r) => r[0] === 'FAIL').length
console.log(`\n${failed === 0 ? 'ALL PASS' : failed + ' FAILED'}`)
process.exit(failed === 0 ? 0 : 1)
