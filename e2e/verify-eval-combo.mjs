// Headed Playwright probe: open the evaluation new-run form and verify the
// pre-set option picker is now a dropdown ▾ (not the inline button row).
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'

function ts() { return new Date().toISOString().slice(11, 23) }
function log(...a) { console.log(`[${ts()}]`, ...a) }

async function main() {
  if (!fs.existsSync(STORAGE)) throw new Error(`auth.json not found at ${STORAGE}`)
  const browser = await chromium.launch({ headless: false, slowMo: 60 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
    storageState: STORAGE,
  })
  const page = await ctx.newPage()

  page.on('console', m => { if (m.type() === 'error') log('CE:', m.text()) })
  page.on('pageerror', e => log('PE:', e.message))

  log('navigate /evaluation')
  await page.goto('/evaluation', { waitUntil: 'domcontentloaded' })
  await page.waitForLoadState('networkidle')

  // Switch to "新建评估"
  const newTab = page.getByRole('button', { name: /新建评估/ }).or(page.getByText('新建评估', { exact: true }))
  if (await newTab.first().isVisible().catch(() => false)) {
    await newTab.first().click()
    log('clicked 新建评估 tab')
  } else {
    log('!! 新建评估 tab not visible — taking shot')
  }

  await page.waitForTimeout(800)

  // Find the URL field by its label
  const urlLabel = page.locator('label', { hasText: '智能体 URL' })
  await urlLabel.scrollIntoViewIfNeeded().catch(() => {})
  const urlInput = urlLabel.locator('input')
  await urlInput.waitFor({ timeout: 5000 })
  log('url input value:', await urlInput.inputValue())

  // The dropdown trigger ▾ should sit inside the same Field
  const trigger = urlLabel.locator('button', { hasText: '▾' })
  const triggerCount = await trigger.count()
  log('▾ triggers in URL field:', triggerCount)

  // The OLD behaviour was a row of preset buttons with the label "预设" — make
  // sure that's gone.
  const oldPresetRow = urlLabel.getByText('预设', { exact: true })
  const oldRowCount = await oldPresetRow.count()
  log('legacy "预设" row count (expect 0):', oldRowCount)

  if (triggerCount > 0) {
    await trigger.first().click()
    await page.waitForTimeout(300)
    const dropdown = page.getByText('预设值', { exact: true })
    const dropdownVisible = await dropdown.isVisible().catch(() => false)
    log('dropdown header "预设值" visible after click:', dropdownVisible)

    // Pick the first option
    const firstOpt = page.locator('button[title]').filter({ hasNotText: '▾' })
    log('option count nearby:', await firstOpt.count())
    await page.screenshot({ path: 'eval-combo-open.png', fullPage: false, clip: { x: 0, y: 0, width: 1440, height: 900 } })
    log('screenshot eval-combo-open.png saved')
  }

  // Click outside to close
  await page.mouse.click(20, 20)
  await page.waitForTimeout(200)
  await page.screenshot({ path: 'eval-combo-closed.png', fullPage: false, clip: { x: 0, y: 0, width: 1440, height: 900 } })

  await browser.close()
  log('done')
}

main().catch(err => { console.error('FATAL:', err); process.exit(1) })
