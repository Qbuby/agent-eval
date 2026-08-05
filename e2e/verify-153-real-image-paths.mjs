// #153 验收：真实图片录入路径（本地文件上传 + Excel 内嵌图片导入）
//
// #148 的脚本走的是「填写链接」路径（data URL 贴进 input）。本脚本补的是用户
// 真正会用的两条路：
//
//   D. 本地文件上传 —— 点「+ 选择文件」走系统文件选择器（Playwright 用
//      setInputFiles 喂真实 png 文件），前端 FileReader 转 data URL 后提交。
//      断言：附件行显示「本地文件（…）」摘要而不是把几 MB base64 塞进 input，
//      缩略图真实解码，落库为 image/base64 canonical block。
//
//   E. Excel 内嵌图导入 —— 上传一个 drawing 锚在第 2 行的 xlsx，第 2 行样例
//      应带图、第 3 行纯文本样例不带图（行号对齐，不错位）。这是
//      xlsx_images.extract_row_images 的端到端路径。
//
// fixture 由 _make_fixtures_153.py 生成（同一个 stamp 串两个文件）。
// 图片走 data URL / 内嵌字节，不依赖外网图床。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const authPath = path.join(root, 'auth.json')
if (!fs.existsSync(authPath)) throw new Error(`缺少登录态文件: ${authPath}`)

const STAMP = process.argv[2]
if (!STAMP) throw new Error('用法: node verify-153-real-image-paths.mjs <stamp>')

const PNG_FIXTURE = path.join(root, '_fixture_153.png')
const XLSX_FIXTURE = path.join(root, '_fixture_153_embedded.xlsx')
for (const f of [PNG_FIXTURE, XLSX_FIXTURE]) {
  if (!fs.existsSync(f)) throw new Error(`缺少 fixture: ${f}（先跑 _make_fixtures_153.py）`)
}

const BENCH_PROJECT_ID = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'  // ep-agent
const CAND_DATASET = 'probe-ds-1785299523343'

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

/** 校验一张缩略图真的解码出来了（naturalWidth>0 才算真渲染） */
async function assertThumbRendered(scope, label) {
  const img = scope.locator('img').first()
  await img.waitFor({ state: 'visible', timeout: 10_000 })
  const w = await img.evaluate(el => el.naturalWidth)
  if (w > 0) pass(`${label}：缩略图真实解码 naturalWidth=${w}`)
  else fail(`${label}：<img> 存在但未解码 naturalWidth=0`)
  return w
}

/** 取 localStorage 里的 access token，供 page.evaluate 内的 fetch 用 */
async function apiGet(url) {
  return page.evaluate(async (u) => {
    const raw = localStorage.getItem('agent-eval-auth')
    const tok = raw ? (JSON.parse(raw).state?.accessToken ?? '') : ''
    const r = await fetch(u, { headers: { Authorization: `Bearer ${tok}` } })
    return { status: r.status, body: r.ok ? await r.json() : null }
  }, url)
}

/** 断言一条样例落库为带图 canonical blocks + question 含 [图片] 占位 */
function assertImageRow(row, label) {
  if (!row) { fail(`${label}：API 未查到样例`); return }
  const qc = row.question_content
  if (!Array.isArray(qc)) {
    fail(`${label}：question_content 不是 blocks 数组，实际=${JSON.stringify(qc)}`)
    return
  }
  const img = qc.find(b => b?.type === 'image')
  if (!img) {
    fail(`${label}：blocks 里没有 image 块: ${JSON.stringify(qc).slice(0, 200)}`)
  } else if (img.source?.type !== 'base64') {
    fail(`${label}：image.source.type=${img.source?.type}，期望 base64`)
  } else {
    pass(`${label}：落库为 canonical blocks（image/base64 ${img.source.media_type}）`)
  }
  if (!(row.question || '').includes('[图片]')) {
    fail(`${label}：question 纯文本投影缺 [图片] 占位: ${JSON.stringify(row.question)}`)
  } else {
    pass(`${label}：question 文本投影含 [图片] 占位`)
  }
}

try {
  // ============ D. 本地文件上传（备选集新增弹窗）============
  console.log('\n=== D. 本地文件上传路径（+ 选择文件）===')
  await page.goto(`/datasets/${encodeURIComponent(CAND_DATASET)}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('登录态失效，跳转 /login')
  await page.waitForTimeout(1500)

  await page.getByRole('button', { name: /添加样例|新增样例|添加/ }).first().click()
  const dlg = page.locator('[role="dialog"]').first()
  await dlg.waitFor({ state: 'visible', timeout: 10_000 })

  const localText = `本地上传-${stamp}`
  await dlg.locator('textarea').first().fill(localText)

  // 「+ 选择文件」点开的是 hidden file input，直接 setInputFiles 喂真实文件。
  const fileBtn = dlg.getByRole('button', { name: /\+ 选择文件/ })
  if (await fileBtn.count() === 0) fail('D1 弹窗里没有「+ 选择文件」按钮')
  else pass('D1 弹窗里有「+ 选择文件」按钮')
  await dlg.locator('input[type="file"]').first().setInputFiles(PNG_FIXTURE)
  await page.waitForTimeout(1200)

  // 本地文件不该把 base64 灌进可见 input（会卡输入法），只显示摘要
  const dlgText = await dlg.innerText()
  if (dlgText.includes('本地文件')) pass('D2 附件行显示「本地文件（…）」摘要，未把 base64 灌进 input')
  else fail(`D2 未见「本地文件」摘要，弹窗文本=${JSON.stringify(dlgText.slice(0, 200))}`)

  await assertThumbRendered(dlg, 'D3 新增弹窗即时预览')
  await page.screenshot({ path: path.join(root, 'shot-153-local-upload-dialog.png') })

  await dlg.getByRole('button', { name: /^(添加|确定|保存|提交)$/ }).first().click()
  await dlg.waitFor({ state: 'hidden', timeout: 20_000 })
  pass('D4 提交成功（弹窗关闭，后端未拒绝）')

  await page.waitForTimeout(1800)
  const localRow = page.locator('tr', { hasText: localText }).first()
  await localRow.waitFor({ state: 'visible', timeout: 20_000 })
  const localRowText = await localRow.innerText()
  if (localRowText.includes('[图片]')) pass('D5 列表渲染出 [图片] 文本占位')
  else fail(`D5 列表未见 [图片] 占位，行文本=${JSON.stringify(localRowText.slice(0, 120))}`)
  await assertThumbRendered(localRow, 'D6 列表行缩略图')

  const dRes = await apiGet(`/api/candidates?dataset_name=${encodeURIComponent(CAND_DATASET)}&page=1&page_size=100&search=${encodeURIComponent(localText)}`)
  const dItems = dRes.body?.items ?? dRes.body?.data ?? []
  assertImageRow(dItems.find(c => (c.question || '').includes(localText)), 'D7 本地上传落库')

  // ============ E. Excel 内嵌图导入（基准集）============
  console.log('\n=== E. Excel 内嵌图片导入 ===')
  await page.goto(`/benchmark/${BENCH_PROJECT_ID}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await page.waitForTimeout(2000)

  // 导入入口：找页面上的文件 input（「导入」按钮通常触发 hidden input）
  const importBtn = page.getByRole('button', { name: /导入/ }).first()
  if (await importBtn.count() > 0) {
    await importBtn.click()
    await page.waitForTimeout(800)
  }
  const fileInputs = page.locator('input[type="file"]')
  const nInputs = await fileInputs.count()
  if (nInputs === 0) throw new Error('基准集页面找不到文件上传 input')
  console.log(`  找到 ${nInputs} 个 file input，用第一个可用的`)
  await fileInputs.first().setInputFiles(XLSX_FIXTURE)

  // 导入可能弹出列映射确认，出现就点确认
  await page.waitForTimeout(2500)
  const confirmBtn = page.getByRole('button', { name: /^(确认导入|开始导入|确认|导入)$/ }).first()
  if (await confirmBtn.count() > 0 && await confirmBtn.isVisible().catch(() => false)) {
    await confirmBtn.click()
    console.log('  点了导入确认按钮')
  }
  await page.waitForTimeout(4000)
  await page.screenshot({ path: path.join(root, 'shot-153-xlsx-import.png') })

  // 核验：带图行有图，纯文本行无图（行号不错位）
  const imgText = `内嵌图导入-带图-${STAMP}`
  const txtText = `内嵌图导入-纯文本-${STAMP}`

  const eImg = await apiGet(`/api/benchmark/${BENCH_PROJECT_ID}/cases?page=1&page_size=50&search=${encodeURIComponent(imgText)}`)
  const eImgItems = eImg.body?.items ?? eImg.body?.data ?? []
  const imgRow = eImgItems.find(c => (c.question || '').includes(imgText))
  assertImageRow(imgRow, 'E1 xlsx 第 2 行（带图）')

  const eTxt = await apiGet(`/api/benchmark/${BENCH_PROJECT_ID}/cases?page=1&page_size=50&search=${encodeURIComponent(txtText)}`)
  const eTxtItems = eTxt.body?.items ?? eTxt.body?.data ?? []
  const txtRow = eTxtItems.find(c => (c.question || '').includes(txtText))
  if (!txtRow) {
    fail('E2 纯文本行：API 未查到（导入可能整体失败）')
  } else if (txtRow.question_content == null) {
    pass('E2 xlsx 第 3 行（纯文本）：question_content 为空，行号未错位')
  } else {
    fail(`E2 纯文本行竟带 question_content=${JSON.stringify(txtRow.question_content).slice(0, 200)}（行号错位）`)
  }

  // UI 上也确认带图行渲染出缩略图
  if (imgRow) {
    const searchBox = page.locator('input[placeholder*="搜索"]').first()
    if (await searchBox.count() > 0) {
      await searchBox.fill(imgText)
      await page.waitForTimeout(2500)
      const benchRow = page.locator('tr', { hasText: imgText }).first()
      if (await benchRow.isVisible().catch(() => false)) {
        const t = await benchRow.innerText()
        if (t.includes('[图片]')) pass('E3 基准集列表渲染出 [图片] 占位')
        else fail(`E3 列表未见 [图片] 占位: ${JSON.stringify(t.slice(0, 120))}`)
        await assertThumbRendered(benchRow, 'E4 基准集列表行缩略图')
        await page.screenshot({ path: path.join(root, 'shot-153-xlsx-row.png') })
      } else {
        fail('E3 搜索后未在列表定位到导入的带图样例')
      }
    }
  }

  console.log(`\n${failed === 0 ? 'VERDICT PASS' : `VERDICT FAIL (${failed} 项)`}`)
} catch (e) {
  console.log('ERROR', e.message)
  await page.screenshot({ path: path.join(root, 'shot-153-error.png') }).catch(() => {})
  failed++
} finally {
  await page.waitForTimeout(1500)
  await browser.close()
  process.exit(failed === 0 ? 0 : 1)
}
