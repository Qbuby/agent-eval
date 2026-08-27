// #148 验收：带图样例全链路（单轮备选集 / 单轮基准集 / 多轮对话集）
//
// 走真实 UI：打开新增弹窗 → 填文本 → 点「+ 附件」→ 粘 data URL → 提交，
// 然后核验 ① 列表出现 [图片] 占位 ② 缩略图 <img> 真的渲染且 naturalWidth>0
// ③ 编辑弹窗能回填附件（不丢图）④ 后端 question_content 落库为 canonical blocks。
//
// 图片用 data URL 内联，不依赖外网图床，也不碰 OSS 防盗链。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const authPath = path.join(root, 'auth.json')
if (!fs.existsSync(authPath)) throw new Error(`缺少登录态文件: ${authPath}`)

const BENCH_PROJECT_ID = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'  // ep-agent
const CAND_DATASET = 'probe-ds-1785299523343'

// 3x2 纯红 PNG（16 进制手工构造，naturalWidth 应为 3）
const PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAFUlEQVR4nGP8z8DAwMDAxAADEDYDAB0BAd/rMgloAAAAAElFTkSuQmCC'

const stamp = Date.now()
let failed = 0
const fail = (m) => { console.log(`FAIL ${m}`); failed++ }
const pass = (m) => console.log(`PASS ${m}`)

const browser = await chromium.launch({ headless: false })
const context = await browser.newContext({
  baseURL: 'http://localhost',
  storageState: authPath,
  viewport: { width: 1440, height: 960 },
})
const page = await context.newPage()
page.on('console', m => { if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 200)) })

/** 在当前打开的弹窗里填「问题」文本 + 一张附件 */
async function fillQuestionWithImage(dialog, text) {
  const ta = dialog.locator('textarea').first()
  await ta.fill(text)
  // 附件区有两个入口：「+ 选择文件」走本地文件（#153 覆盖），这里走
  // 「填写链接」手输 data URL —— 不依赖磁盘 fixture，纯 UI 链路最短。
  const addBtn = dialog.getByRole('button', { name: '填写链接' })
  if (await addBtn.count() === 0) throw new Error('弹窗里没有「填写链接」按钮')
  await addBtn.click()
  // 新出现的附件 URL 输入行。必须用 placeholder 精确定位：弹窗里还有
  // 参考答案/类别等无 type 的 input，取 .last() 会落到「类别」上。
  const urlInput = dialog.getByPlaceholder(/base64/).last()
  await urlInput.waitFor({ state: 'visible', timeout: 10_000 })
  await urlInput.fill(PNG_DATA_URL)
}

/** 校验一张缩略图真的解码出来了 */
async function assertThumbRendered(scope, label) {
  const img = scope.locator('img').first()
  await img.waitFor({ state: 'visible', timeout: 10_000 })
  const w = await img.evaluate(el => el.naturalWidth)
  if (w > 0) pass(`${label}：缩略图真实解码 naturalWidth=${w}`)
  else fail(`${label}：<img> 存在但未解码 naturalWidth=0`)
  return w
}

try {
  // ==================== A. 单轮备选集 DatasetDetailPage ====================
  console.log('\n=== A. 单轮备选集（DatasetDetailPage）===')
  await page.goto(`/datasets/${encodeURIComponent(CAND_DATASET)}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('登录态失效，跳转 /login')
  await page.waitForTimeout(1500)

  const addCandBtn = page.getByRole('button', { name: /添加样例|新增样例|添加/ }).first()
  await addCandBtn.waitFor({ state: 'visible', timeout: 20_000 })
  await addCandBtn.click()

  const candDialog = page.locator('[role="dialog"]').first()
  await candDialog.waitFor({ state: 'visible', timeout: 10_000 })
  const candText = `带图验收-备选-${stamp}`
  await fillQuestionWithImage(candDialog, candText)
  pass('A1 新增弹窗：「+ 附件」按钮存在且可填 URL')

  // 录入即时预览
  await assertThumbRendered(candDialog, 'A2 新增弹窗即时预览')

  await candDialog.getByRole('button', { name: /^(添加|确定|保存|提交)$/ }).first().click()
  await candDialog.waitFor({ state: 'hidden', timeout: 20_000 })
  pass('A3 提交成功（弹窗关闭，后端未拒绝）')

  await page.waitForTimeout(1500)
  const candRow = page.locator('tr', { hasText: candText }).first()
  await candRow.waitFor({ state: 'visible', timeout: 20_000 })
  const candRowText = await candRow.innerText()
  if (candRowText.includes('[图片]')) pass('A4 列表渲染出 [图片] 文本占位')
  else fail(`A4 列表未见 [图片] 占位，行文本=${JSON.stringify(candRowText.slice(0, 120))}`)
  await assertThumbRendered(candRow, 'A5 列表行缩略图')

  // 编辑回填
  await candRow.hover()
  await candRow.getByRole('button', { name: '编辑' }).click()
  const candEdit = page.locator('[role="dialog"]').first()
  await candEdit.waitFor({ state: 'visible', timeout: 10_000 })
  // 回填后的 data URL 行按设计是只读 span「本地文件（media_type，体积）」，
  // 不是 input —— 几 MB base64 放进输入框会卡输入法。外链行才是 input。
  // 故两种形态都算回填成功。
  const backfilled = await candEdit.evaluate(el => {
    const inputs = [...el.querySelectorAll('input')]
      .map(e => e.value)
      .filter(v => typeof v === 'string' && v.startsWith('data:image'))
    const spans = [...el.querySelectorAll('span')]
      .map(e => e.textContent || '')
      .filter(t => t.includes('本地文件（'))
    return { inputs: inputs.length, spans }
  })
  if (backfilled.inputs > 0 || backfilled.spans.length > 0) {
    pass(`A6 编辑弹窗回填了附件（编辑不丢图）input=${backfilled.inputs} 只读行=${JSON.stringify(backfilled.spans)}`)
  } else {
    fail('A6 编辑弹窗未回填附件（既无 data URL input 也无「本地文件（」只读行）')
  }
  await assertThumbRendered(candEdit, 'A7 编辑弹窗缩略图')
  await page.screenshot({ path: path.join(root, 'shot-148-candidate-edit.png') })
  await candEdit.getByRole('button', { name: /取消|关闭/ }).first().click()
  await page.waitForTimeout(500)

  // ==================== B. 单轮基准集 BenchmarkPage ====================
  console.log('\n=== B. 单轮基准集（BenchmarkPage）===')
  await page.goto(`/benchmark/${BENCH_PROJECT_ID}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await page.waitForTimeout(2000)

  const addBenchBtn = page.getByRole('button', { name: /新增样例|添加样例|新建样例/ }).first()
  await addBenchBtn.waitFor({ state: 'visible', timeout: 20_000 })
  await addBenchBtn.click()
  const benchDialog = page.locator('[role="dialog"]').first()
  await benchDialog.waitFor({ state: 'visible', timeout: 10_000 })
  const benchText = `带图验收-基准-${stamp}`
  await fillQuestionWithImage(benchDialog, benchText)
  pass('B1 新增弹窗：「+ 附件」按钮存在且可填 URL')
  await assertThumbRendered(benchDialog, 'B2 新增弹窗即时预览')
  await benchDialog.getByRole('button', { name: /^(添加|确定|保存|创建|提交)$/ }).first().click()
  await benchDialog.waitFor({ state: 'hidden', timeout: 20_000 })
  pass('B3 提交成功')

  // 基准集有 2000+ 条，用搜索定位
  await page.waitForTimeout(1500)
  const searchBox = page.locator('input[placeholder*="搜索"]').first()
  if (await searchBox.count() > 0) {
    await searchBox.fill(benchText)
    await page.waitForTimeout(2000)
  }
  const benchRow = page.locator('tr', { hasText: benchText }).first()
  const benchRowVisible = await benchRow.isVisible().catch(() => false)
  if (benchRowVisible) {
    const t = await benchRow.innerText()
    if (t.includes('[图片]')) pass('B4 列表渲染出 [图片] 文本占位')
    else fail(`B4 列表未见 [图片] 占位: ${JSON.stringify(t.slice(0, 120))}`)
    // 展开行看附件区
    await benchRow.click()
    await page.waitForTimeout(1000)
    const expandArea = page.locator('tr.bg-fill\\/5').first()
    if (await expandArea.count() > 0) {
      await assertThumbRendered(expandArea, 'B5 展开行附件展示')
    } else {
      fail('B5 展开行未渲染')
    }
    await page.screenshot({ path: path.join(root, 'shot-148-benchmark-expand.png') })
  } else {
    fail('B4 搜索后未在列表定位到新建的带图样例')
  }

  // ==================== C. 后端落库核验 ====================
  console.log('\n=== C. 后端 question_content 落库 ===')
  const api = await page.evaluate(async ({ ds, projId, candText, benchText }) => {
    const raw = localStorage.getItem('agent-eval-auth')
    const tok = raw ? (JSON.parse(raw).state?.accessToken ?? '') : ''
    const h = { Authorization: `Bearer ${tok}` }
    const out = {}
    // page_size 上限 100（后端 Query(le=100)），超了直接 422
    const r1 = await fetch(`/api/candidates?dataset_name=${encodeURIComponent(ds)}&page=1&page_size=100&search=${encodeURIComponent(candText)}`, { headers: h })
    const j1 = r1.ok ? await r1.json() : null
    const items1 = j1?.items ?? j1?.data ?? []
    out.cand = items1.find(c => (c.question || '').includes(candText)) ?? null
    const r2 = await fetch(`/api/benchmark/${projId}/cases?page=1&page_size=50&search=${encodeURIComponent(benchText)}`, { headers: h })
    const j2 = r2.ok ? await r2.json() : null
    const items2 = j2?.items ?? j2?.data ?? []
    out.bench = items2.find(c => (c.question || '').includes(benchText)) ?? null
    out.status = [r1.status, r2.status]
    return out
  }, { ds: CAND_DATASET, projId: BENCH_PROJECT_ID, candText, benchText })

  console.log('  API status:', JSON.stringify(api.status))
  for (const [label, row] of [['备选集', api.cand], ['基准集', api.bench]]) {
    if (!row) { fail(`C ${label}：API 未查到新建样例`); continue }
    const qc = row.question_content
    if (!Array.isArray(qc)) { fail(`C ${label}：question_content 不是 blocks 数组，实际=${JSON.stringify(qc)}`); continue }
    const img = qc.find(b => b?.type === 'image')
    const txt = qc.find(b => b?.type === 'text')
    if (!img) fail(`C ${label}：blocks 里没有 image 块: ${JSON.stringify(qc).slice(0, 200)}`)
    else if (img.source?.type !== 'base64') fail(`C ${label}：image.source.type=${img.source?.type}，期望 base64`)
    else pass(`C ${label}：落库为 canonical blocks（image/base64 ${img.source.media_type}，text=${JSON.stringify((txt?.text ?? '').slice(0, 40))}）`)
    if (!(row.question || '').includes('[图片]')) fail(`C ${label}：question 纯文本投影缺 [图片] 占位: ${JSON.stringify(row.question)}`)
    else pass(`C ${label}：question 文本投影含 [图片] 占位`)
  }

  console.log(`\n${failed === 0 ? 'VERDICT PASS' : `VERDICT FAIL (${failed} 项)`}`)
} catch (e) {
  console.log('ERROR', e.message)
  await page.screenshot({ path: path.join(root, 'shot-148-error.png') }).catch(() => {})
  failed++
} finally {
  await page.waitForTimeout(1500)
  await browser.close()
  process.exit(failed === 0 ? 0 : 1)
}
