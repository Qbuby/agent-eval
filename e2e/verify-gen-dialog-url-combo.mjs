// Headed Playwright probe: the "agent generate answer" dialog's URL field must
// be a combo (free-text input + dropdown of configured target_agent.endpoint_url).
//
// NOTE: all CJK literals are written as \uXXXX escapes on purpose. This repo's
// toolchain has produced phantom reads on files containing raw CJK, so keeping
// this probe pure-ASCII makes its content verifiable byte-for-byte.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const PROJECT_ID = process.env.PROJ || 'e65c39e4-fd26-4bad-a43a-5bc8caba16b9'
const EXEC = process.env.PW_CHROMIUM
  || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'

const T_URL_LABEL = '\u667a\u80fd\u4f53 URL'          // "zhinengti URL"
const T_PICK = '\u9009\u62e9\u9884\u8bbe\u503c'        // "xuanze yushezhi"
const T_HEADER = '\u9884\u8bbe\u503c'                  // "yushezhi"
const T_GEN_BTN = 'agent\u751f\u6210\u7b54\u6848'      // "agent shengcheng dan'an"
const T_KEY_LABEL = 'API Key'

let fails = 0
function ts() { return new Date().toISOString().slice(11, 23) }
function log(...a) { console.log(`[${ts()}]`, ...a) }
function check(ok, msg) {
  if (ok) log('PASS  ' + msg)
  else { fails++; log('FAIL  ' + msg) }
}

async function main() {
  if (!fs.existsSync(STORAGE)) throw new Error(`auth.json not found at ${STORAGE}`)
  const browser = await chromium.launch({ headless: false, slowMo: 80, executablePath: EXEC })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1500, height: 950 },
    storageState: STORAGE,
  })
  const page = await ctx.newPage()
  page.on('console', m => { if (m.type() === 'error') log('CE:', m.text()) })
  page.on('pageerror', e => log('PE:', e.message))

  log('navigate /benchmark/' + PROJECT_ID)
  await page.goto(`/benchmark/${PROJECT_ID}`, { waitUntil: 'domcontentloaded' })
  await page.waitForLoadState('networkidle')
  log('url now:', page.url())

  // Guard: an expired JWT bounces to /login and every later assertion would be
  // a meaningless failure.
  if (/\/login/.test(page.url())) {
    throw new Error('redirected to /login - auth.json expired, run refresh-auth.mjs')
  }

  // Select one case so the generate button leaves its disabled state.
  const rowBoxes = page.locator('tbody input[type="checkbox"]')
  await rowBoxes.first().waitFor({ timeout: 15_000 })
  log('row checkboxes:', await rowBoxes.count())
  await rowBoxes.first().check()

  const genBtn = page.getByRole('button', { name: new RegExp(T_GEN_BTN) })
  await genBtn.first().waitFor({ timeout: 10_000 })
  check(await genBtn.first().isEnabled(), 'generate button enabled after selecting a case')
  await genBtn.first().click()

  // ---- the dialog ----
  const urlLabel = page.locator('label', { hasText: T_URL_LABEL })
  await urlLabel.first().waitFor({ timeout: 10_000 })
  const urlInput = urlLabel.first().locator('input')
  await urlInput.waitFor({ timeout: 10_000 })
  const prefilled = await urlInput.inputValue()
  log('url input prefilled with:', JSON.stringify(prefilled))
  check(await urlInput.isEditable(), 'url field is still free-text editable')

  // The picker trigger lives inside the same label wrapper.
  const trigger = urlLabel.first().locator(`button[aria-label="${T_PICK}"]`)
  const triggerCount = await trigger.count()
  check(triggerCount === 1, `exactly one dropdown trigger in url field (got ${triggerCount})`)
  if (triggerCount === 0) throw new Error('no dropdown trigger rendered - nothing further to verify')

  check(
    (await trigger.first().getAttribute('aria-expanded')) === 'false',
    'trigger starts collapsed (aria-expanded=false)',
  )

  await page.screenshot({ path: 'gen-dialog-url-combo-closed.png' })

  log('open the dropdown')
  await trigger.first().click()
  const listbox = page.locator('[role="listbox"]')
  await listbox.first().waitFor({ state: 'visible', timeout: 5000 })
  check(true, 'listbox visible after clicking trigger')
  check(
    (await trigger.first().getAttribute('aria-expanded')) === 'true',
    'trigger reports aria-expanded=true while open',
  )
  check(
    (await listbox.first().getAttribute('aria-label')) === T_HEADER,
    'listbox is labelled as the preset list',
  )

  const opts = listbox.first().locator('[role="option"]')
  const optCount = await opts.count()
  log('option count:', optCount)
  check(optCount === 6, `lists all 6 configured endpoint_url presets (got ${optCount})`)
  for (let i = 0; i < optCount; i++) {
    log(`  opt[${i}] title=${JSON.stringify(await opts.nth(i).getAttribute('title'))}`)
  }

  await page.screenshot({ path: 'gen-dialog-url-combo-open.png' })

  // Pick a preset that differs from the prefilled value, then assert writeback.
  let targetIdx = -1
  let targetVal = ''
  for (let i = 0; i < optCount; i++) {
    const t = (await opts.nth(i).getAttribute('title')) || ''
    if (t && t !== prefilled) { targetIdx = i; targetVal = t; break }
  }
  check(targetIdx >= 0, 'found a preset different from the prefilled value')
  if (targetIdx >= 0) {
    log(`click opt[${targetIdx}] -> ${targetVal}`)
    await opts.nth(targetIdx).click()
    await page.waitForTimeout(250)
    const after = await urlInput.inputValue()
    log('url input value after pick:', JSON.stringify(after))
    check(after === targetVal, 'picking a preset writes it back into the input')
    check(!(await listbox.first().isVisible().catch(() => false)), 'dropdown closes after picking')
  }

  // Typing must still work - this is a combo, not a locked select.
  await urlInput.fill('http://typed-by-hand.example/api/agent')
  check(
    (await urlInput.inputValue()) === 'http://typed-by-hand.example/api/agent',
    'free-text typing still accepted after using the dropdown',
  )

  // Keyboard path: reopen, ArrowDown + Enter should commit a value.
  await trigger.first().click()
  await listbox.first().waitFor({ state: 'visible', timeout: 5000 })
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(250)
  const kbVal = await urlInput.inputValue()
  log('url input value after keyboard pick:', JSON.stringify(kbVal))
  check(kbVal.startsWith('http') && kbVal !== 'http://typed-by-hand.example/api/agent',
    'ArrowDown+Enter commits a preset via keyboard')

  // Informational: api_key is stored single-value ({"v": ""}) with no options
  // array, so by design its picker stays hidden.
  const keyLabel = page.locator('label', { hasText: T_KEY_LABEL })
  const keyTrigger = keyLabel.first().locator(`button[aria-label="${T_PICK}"]`)
  log('INFO api key picker count (expected 0, api_key has no options):', await keyTrigger.count())

  await page.screenshot({ path: 'gen-dialog-url-combo-final.png' })
  await browser.close()

  log(fails === 0 ? 'ALL CHECKS PASSED' : `${fails} CHECK(S) FAILED`)
  process.exit(fails === 0 ? 0 : 1)
}

main().catch(err => { console.error('FATAL:', err); process.exit(1) })
