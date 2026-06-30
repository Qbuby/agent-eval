// One-off: open evaluation run detail in headed Chromium, verify the page
// renders without crashing on legacy rows that have no CoT steps.
import { chromium } from '@playwright/test'

const FRONTEND = 'http://localhost:3000'
const BACKEND = 'http://localhost:8000'
const RUN_ID = '29af92cf-85bc-45cd-84fe-e6297a8c2775'
const USER = { username: 'playwright_test', password: 'pwTest!2026' }

async function login() {
  const r = await fetch(`${BACKEND}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(USER),
  })
  if (!r.ok) throw new Error(`login failed ${r.status}`)
  return r.json()
}

const tokens = await login()
console.log('[verify] login ok, access token len=', tokens.access_token.length)

const browser = await chromium.launch({ headless: false, slowMo: 200 })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

const errors = []
page.on('pageerror', (e) => { errors.push('pageerror: ' + e.message) })
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push('console.error: ' + msg.text())
})

// Seed zustand-persist storage so ProtectedRoute lets us through.
await page.goto(FRONTEND)
await page.evaluate((tok) => {
  localStorage.setItem(
    'agent-eval-auth',
    JSON.stringify({
      state: {
        accessToken: tok.access_token,
        refreshToken: tok.refresh_token,
        user: null,
      },
      version: 0,
    }),
  )
}, tokens)

await page.goto(`${FRONTEND}/evaluation/runs/${RUN_ID}`)
await page.waitForLoadState('networkidle', { timeout: 15000 })

// Check key elements
const title = await page.title()
console.log('[verify] title=', title)

const url = page.url()
console.log('[verify] url=', url)

// Look for evaluation-detail markers
const hasEvaluationName = await page.locator('text=/0519/').count()
console.log('[verify] run-name occurrences=', hasEvaluationName)

const cotSections = await page.locator('text=/思维链/').count()
console.log('[verify] CoT-section occurrences=', cotSections,
  '(should be 0 — no legacy run has full_trace.steps)')

const toolCallsBlocks = await page.locator('text=/工具调用|Tool Calls/i').count()
console.log('[verify] tool-call section occurrences=', toolCallsBlocks)

const errorBanner = await page.locator('text=/出错|加载失败|error/i').count()
console.log('[verify] visible error banners=', errorBanner)

// Expand first row to make the per-case detail render
const expandBtns = page.locator('button:has-text("展开"), [aria-label="展开"], button[title*="展开"]')
const expandCount = await expandBtns.count()
console.log('[verify] expandable rows=', expandCount)
if (expandCount > 0) {
  await expandBtns.first().click().catch(() => {})
  await page.waitForTimeout(600)
}

const screenshotPath = 'e2e/test-results/verify-cot-detail.png'
await page.screenshot({ path: screenshotPath, fullPage: true })
console.log('[verify] screenshot saved →', screenshotPath)

if (errors.length) {
  console.log('[verify] CONSOLE / PAGE ERRORS:')
  errors.forEach((e) => console.log('  ', e))
} else {
  console.log('[verify] no console/page errors')
}

await page.waitForTimeout(2500)
await browser.close()
console.log('[verify] done')
