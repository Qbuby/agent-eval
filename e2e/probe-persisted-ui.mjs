import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const STORAGE = path.join(SCRIPT_DIR, 'auth.json')
const USER_FILE = path.join(SCRIPT_DIR, 'test-user.json')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'
const browser = await chromium.launch({ headless: false, executablePath: EXEC })
// 每轮验收都从空上下文真实登录，避免过期 refresh token 与页面 401 拦截器竞争。
const context = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const out = { pages: {}, api: {} }

async function api(pathname) {
  return page.evaluate(async (pathname) => {
    const raw = localStorage.getItem('agent-eval-auth')
    const parsed = raw ? JSON.parse(raw) : null
    const token = parsed?.state?.accessToken || localStorage.getItem('access_token')
    const response = await fetch(pathname, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    const text = await response.text()
    let body
    try { body = JSON.parse(text) } catch { body = text.slice(0, 500) }
    return { status: response.status, body }
  }, pathname)
}

await page.goto('/login')
const user = JSON.parse(fs.readFileSync(USER_FILE, 'utf8'))
await page.getByPlaceholder('输入用户名').fill(user.username)
await page.getByPlaceholder('输入密码').fill(user.password)
await page.getByRole('button', { name: /继续|登录/ }).click()
await page.waitForURL(url => !url.pathname.includes('/login'), { timeout: 15_000 })
await page.waitForLoadState('networkidle')
await context.storageState({ path: STORAGE })
out.login = { url: page.url(), title: await page.title(), localStorageKeys: await page.evaluate(() => Object.keys(localStorage)) }

out.api.projects = await api('/api/projects')
out.api.datasets = await api('/api/datasets')
out.api.evaluators = await api('/api/evaluation/evaluators?active_only=true')

for (const [name, route] of [['candidates', '/datasets'], ['conversations', '/conversations'], ['evaluation', '/evaluation']]) {
  await page.goto(route)
  await page.waitForLoadState('networkidle')
  out.pages[name] = {
    url: page.url(),
    text: (await page.locator('body').innerText()).slice(0, 4000),
    buttons: await page.getByRole('button').allTextContents(),
    links: await page.getByRole('link').allTextContents(),
  }
}

console.log(JSON.stringify(out, null, 2))
await browser.close()
