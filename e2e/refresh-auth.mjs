// One-off helper: register a fresh e2e user and persist auth.json.
// Standalone (no agent probe) — use when the existing storageState's JWT
// has expired and you need to re-run a verify-* probe.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const TS = Date.now()
const USER = {
  username: `e2e_${TS}`,
  email: `e2e_${TS}@example.com`,
  password: 'Password123!',
}

function ts() { return new Date().toISOString().slice(11, 23) }
function log(...a) { console.log(`[${ts()}]`, ...a) }

async function main() {
  const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1140\\chrome-win\\chrome.exe'
  const browser = await chromium.launch({ headless: true, executablePath: EXEC })
  const ctx = await browser.newContext({ baseURL: BASE })
  const page = await ctx.newPage()

  log('register', USER.username)
  await page.goto('/register')
  await page.getByPlaceholder('选择用户名').fill(USER.username)
  await page.getByPlaceholder('you@example.com').fill(USER.email)
  await page.getByPlaceholder('至少 8 位，含字母和数字').fill(USER.password)
  await page.getByPlaceholder('再次输入密码').fill(USER.password)
  await page.getByRole('button', { name: /注册|创建/ }).click()
  await page.waitForURL(/\/login/, { timeout: 15_000 })

  log('login')
  await page.getByPlaceholder('输入用户名').fill(USER.username)
  await page.getByPlaceholder('输入密码').fill(USER.password)
  await page.getByRole('button', { name: '继续' }).click()
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 })

  await ctx.storageState({ path: STORAGE })
  await browser.close()
  fs.writeFileSync(path.resolve('test-user.json'), JSON.stringify(USER))
  log('wrote', STORAGE)
}

main().catch(err => { console.error('FATAL:', err); process.exit(1) })
