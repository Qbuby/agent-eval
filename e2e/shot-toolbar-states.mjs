// Screenshot the toolbar in both states so the disabled vs enabled contrast can
// be eyeballed, not just measured. ASCII-only source (CJK phantom-read box).
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = 'http://localhost'
const DATASET = process.argv[2] || 'probe-ds-1785299523343'
const AUTH = new URL('./auth.json', import.meta.url)

const browser = await chromium.launch({ headless: false })
const ctx = await browser.newContext({
  storageState: JSON.parse(fs.readFileSync(AUTH, 'utf8')),
  viewport: { width: 1600, height: 900 },
})
const page = await ctx.newPage()
await page.goto(`${BASE}/datasets/${DATASET}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

// Toolbar row = the flex container holding the buttons; clip to it.
const btn = page.getByRole('button', { name: /agent\s*生成答案/i }).first()
await btn.waitFor()
const bar = btn.locator('xpath=..')
const box = await bar.boundingBox()
const clip = { x: Math.max(0, box.x - 40), y: Math.max(0, box.y - 14), width: Math.min(1600 - box.x + 40, box.width + 80), height: box.height + 28 }

await page.screenshot({ path: 'e2e/toolbar-disabled.png', clip })
console.log('DISABLED_SHOT=e2e/toolbar-disabled.png clip=' + JSON.stringify(clip))

await page.locator('table tbody tr').first().locator('input[type=checkbox]').check()
await page.waitForTimeout(400)
const box2 = await bar.boundingBox()
const clip2 = { x: Math.max(0, box2.x - 40), y: Math.max(0, box2.y - 14), width: Math.min(1600 - box2.x + 40, box2.width + 80), height: box2.height + 28 }
await page.screenshot({ path: 'e2e/toolbar-enabled.png', clip: clip2 })
console.log('ENABLED_SHOT=e2e/toolbar-enabled.png')

await browser.close()
