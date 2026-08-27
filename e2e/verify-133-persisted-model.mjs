// #133 验收：双模对比 + 使用已有回复 时，A/B 两侧各自有「模型名称（用于结果展示）」输入框，
// 且可独立填写不同值。修复前两侧共用硬编码 model: 'persisted-reply'，结果页无法区分。
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const USER_FILE = path.join(SCRIPT_DIR, 'test-user.json')
const SHOT = path.join(SCRIPT_DIR, 'shot-133-persisted-model.png')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'

const browser = await chromium.launch({ headless: false, executablePath: EXEC })
const context = await browser.newContext({ baseURL: BASE, viewport: { width: 1600, height: 1000 } })
const page = await context.newPage()
const out = { steps: [], checks: {} }
const step = (name, info) => { out.steps.push({ name, ...info }) }

try {
  await page.goto('/login')
  const user = JSON.parse(fs.readFileSync(USER_FILE, 'utf8'))
  await page.getByPlaceholder('输入用户名').fill(user.username)
  await page.getByPlaceholder('输入密码').fill(user.password)
  await page.getByRole('button', { name: /继续|登录/ }).click()
  await page.waitForURL(url => !url.pathname.includes('/login'), { timeout: 15_000 })
  step('login', { url: page.url() })

  await page.goto('/evaluation')
  await page.waitForLoadState('networkidle')

  // 切到双模对比
  const cmpTab = page.getByText('双模对比', { exact: false }).first()
  await cmpTab.click()
  await page.waitForTimeout(600)
  step('comparative-tab', { visible: await cmpTab.isVisible() })

  // 基线：切换前不应存在该输入框
  out.checks.beforeCount = await page.getByPlaceholder('例如 deepseek-v3 / claude-sonnet-4').count()

  // A/B 两侧都切到「使用已有回复」
  const persistedBtns = page.getByRole('button', { name: /使用已有回复/ })
  const nBtn = await persistedBtns.count()
  step('persisted-buttons', { count: nBtn })
  for (let i = 0; i < nBtn; i++) {
    await persistedBtns.nth(i).click()
    await page.waitForTimeout(400)
  }

  const inputs = page.getByPlaceholder('例如 deepseek-v3 / claude-sonnet-4')
  out.checks.afterCount = await inputs.count()

  // 独立填写，确认两侧 state 不串
  if (out.checks.afterCount >= 2) {
    await inputs.nth(0).fill('deepseek-v3')
    await inputs.nth(1).fill('claude-sonnet-4')
    await page.waitForTimeout(300)
    out.checks.valueA = await inputs.nth(0).inputValue()
    out.checks.valueB = await inputs.nth(1).inputValue()
    out.checks.independent = out.checks.valueA === 'deepseek-v3' && out.checks.valueB === 'claude-sonnet-4'
  }

  out.checks.labelCount = await page.getByText('模型名称（用于结果展示）').count()
  out.checks.hintCount = await page.getByText('留空则显示为 persisted-reply').count()

  await page.screenshot({ path: SHOT, fullPage: true })
  out.shot = SHOT

  const pass = out.checks.beforeCount === 0
    && out.checks.afterCount >= 2
    && out.checks.independent === true
    && out.checks.labelCount >= 2
  out.RESULT = pass ? 'PASS' : 'FAIL'
} catch (err) {
  out.RESULT = 'ERROR'
  out.error = String(err).slice(0, 800)
  try { await page.screenshot({ path: SHOT, fullPage: true }); out.shot = SHOT } catch {}
}

console.log(JSON.stringify(out, null, 2))
await browser.close()
