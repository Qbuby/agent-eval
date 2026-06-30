// Verify the new run (post-fix) renders the CoT timeline.
import { chromium } from '@playwright/test'

const FRONTEND = 'http://localhost:3000'
const BACKEND = 'http://localhost:8000'
const RUN_ID = 'e7dc5149-9a93-4bbc-a99f-4e3e708ad905'
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
console.log('[verify-new] login ok')

const browser = await chromium.launch({ headless: false, slowMo: 200 })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

const errors = []
page.on('pageerror', (e) => { errors.push('pageerror: ' + e.message) })
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push('console.error: ' + msg.text())
})

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
await page.waitForLoadState('networkidle', { timeout: 20000 })

console.log('[verify-new] url=', page.url())

// Expand the first result row by clicking the <tr> itself (whole row is the toggle).
const rows = page.locator('table tbody tr')
const rowCount = await rows.count()
console.log('[verify-new] result rows=', rowCount)
if (rowCount > 0) {
  await rows.first().click().catch(() => {})
  await page.waitForTimeout(800)
}

// Now look for the CoT block.
const cotHeader = await page.locator('text=/思维链/').count()
console.log('[verify-new] CoT-header occurrences=', cotHeader, '(should be > 0)')

// Pull the visible text near the CoT header for a sanity check.
if (cotHeader > 0) {
  const first = page.locator('text=/思维链/').first()
  const text = await first.textContent()
  console.log('[verify-new] CoT header text=', JSON.stringify(text))
}

// Count step markers within the timeline (thought / tool_call / answer labels).
const stepMarkers = await page.locator('text=/思考|工具调用|回答/').count()
console.log('[verify-new] step-marker occurrences=', stepMarkers)

const errorBanner = await page.locator('text=/出错|加载失败/').count()
console.log('[verify-new] visible error banners=', errorBanner)

const screenshotPath = 'e2e/test-results/verify-cot-new-run.png'
await page.screenshot({ path: screenshotPath, fullPage: true })
console.log('[verify-new] screenshot saved →', screenshotPath)

if (errors.length) {
  console.log('[verify-new] CONSOLE / PAGE ERRORS:')
  errors.forEach((e) => console.log('  ', e))
} else {
  console.log('[verify-new] no console/page errors')
}

await page.waitForTimeout(2500)
await browser.close()
console.log('[verify-new] done')
