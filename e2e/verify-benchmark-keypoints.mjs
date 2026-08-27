// 验收：benchmark 数据集样例的「关键点」在 UI 上真实渲染
// 关键点只在展开行内渲染，所以必须先点开某一行
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const authPath = path.join(root, 'auth.json')
if (!fs.existsSync(authPath)) throw new Error(`缺少登录态文件: ${authPath}`)

const PROJECT_ID = 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'

const browser = await chromium.launch({ headless: false })
const context = await browser.newContext({
  baseURL: 'http://localhost',
  storageState: authPath,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

let failed = 0
const fail = (msg) => { console.log(`FAIL ${msg}`); failed++ }
const pass = (msg) => console.log(`PASS ${msg}`)

try {
  await page.goto(`/benchmark/${PROJECT_ID}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('验收登录态已失效，页面跳转到 /login')

  // 等列表渲染出可展开的行
  const rows = page.locator('tr[title*="点击展开"]')
  await rows.first().waitFor({ state: 'visible', timeout: 30_000 })
  const rowCount = await rows.count()
  pass(`列表渲染出 ${rowCount} 行样例`)

  // 逐行展开，找到第一条带关键点的样例（6/2098 为空是既知的规则性排除）
  let found = null
  const probe = Math.min(rowCount, 8)
  for (let i = 0; i < probe; i++) {
    await rows.nth(i).click()
    const kp = page.getByText('关键点：').first()
    try {
      await kp.waitFor({ state: 'visible', timeout: 5_000 })
      const text = await kp.locator('xpath=following-sibling::span[1]').innerText()
      found = { index: i, text }
      break
    } catch {
      await rows.nth(i).click() // 收起，试下一行
    }
  }

  if (!found) {
    fail(`前 ${probe} 行展开后都没渲染出「关键点：」`)
  } else {
    pass(`第 ${found.index + 1} 行展开后渲染出关键点`)
    const t = found.text.trim()
    if (t.length < 10) fail(`关键点内容过短，疑似空转: ${JSON.stringify(t)}`)
    else pass(`关键点内容为真实要点(${t.length} 字符): ${t.slice(0, 120)}${t.length > 120 ? '…' : ''}`)
  }

  await page.screenshot({ path: path.join(root, 'shot-benchmark-keypoints.png'), fullPage: false })
} catch (e) {
  fail(`异常: ${e.message}`)
} finally {
  await browser.close()
}

console.log(`\nRESULT=${failed === 0 ? 'PASS' : 'FAIL'} (${failed})`)
process.exit(failed === 0 ? 0 : 1)
