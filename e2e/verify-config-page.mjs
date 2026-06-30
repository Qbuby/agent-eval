// Headed Playwright probe: open the config page, capture network + console
// signals, and report whether table rows render. Used for ad-hoc verification
// of the multi-value config UI rebuild.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const STORAGE = path.resolve('auth.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const SLEEP_AFTER_LOAD_MS = Number(process.env.SOAK_MS || 12_000)

function ts() {
  return new Date().toISOString().slice(11, 23)
}
function log(...a) { console.log(`[${ts()}]`, ...a) }

async function main() {
  if (!fs.existsSync(STORAGE)) {
    throw new Error(`auth.json not found at ${STORAGE}`)
  }
  const browser = await chromium.launch({ headless: false, slowMo: 80 })
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
    storageState: STORAGE,
  })
  const page = await ctx.newPage()

  const consoleErrors = []
  const pageErrors = []
  const failedRequests = []
  const configRequests = []

  page.on('console', msg => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text())
      log('CONSOLE ERROR:', msg.text())
    } else if (msg.type() === 'warning') {
      log('CONSOLE WARN:', msg.text())
    }
  })
  page.on('pageerror', err => {
    pageErrors.push(err.message)
    log('PAGE ERROR:', err.message)
  })
  page.on('requestfailed', req => {
    failedRequests.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText}`)
    log('REQ FAILED:', req.method(), req.url(), req.failure()?.errorText)
  })
  page.on('response', async resp => {
    const url = resp.url()
    if (url.includes('/api/config')) {
      configRequests.push({
        url, status: resp.status(), method: resp.request().method(),
      })
      log('API:', resp.request().method(), url, '→', resp.status())
    }
  })

  log('navigating to /config')
  await page.goto('/config', { waitUntil: 'domcontentloaded' })
  log('DOMContentLoaded')

  // Wait for either rows or "loading" indicator to appear
  try {
    await page.waitForSelector('table tbody tr', { timeout: 10_000 })
  } catch (e) {
    log('!! no <tr> in tbody after 10s')
  }

  // Snapshot 1 — immediately after load
  const snap1 = await snapshot(page)
  log('SNAPSHOT t=0:', JSON.stringify(snap1))

  // Hold the page open and re-sample to detect "white-screen after a while"
  log(`soaking for ${SLEEP_AFTER_LOAD_MS}ms to watch for white-screen...`)
  await page.waitForTimeout(SLEEP_AFTER_LOAD_MS)
  const snap2 = await snapshot(page)
  log(`SNAPSHOT t=${SLEEP_AFTER_LOAD_MS}ms:`, JSON.stringify(snap2))

  await page.screenshot({ path: 'config-page-after-soak.png', fullPage: true })
  log('saved screenshot config-page-after-soak.png')

  log('--- summary ---')
  log('console errors:', consoleErrors.length)
  log('page errors:', pageErrors.length)
  log('failed requests:', failedRequests.length)
  log('config api calls:', configRequests.length)
  for (const r of configRequests) log('   ', r.method, r.url, '→', r.status)
  for (const e of consoleErrors) log('   ce:', e)
  for (const e of pageErrors) log('   pe:', e)
  for (const f of failedRequests) log('   rf:', f)

  await browser.close()
}

async function snapshot(page) {
  return await page.evaluate(() => {
    const rows = document.querySelectorAll('table tbody tr')
    const cells = document.querySelectorAll('table tbody tr td')
    const rootChildren = document.getElementById('root')?.children?.length ?? -1
    const bodyText = (document.body.innerText || '').slice(0, 200)
    return {
      url: location.href,
      title: document.title,
      rowCount: rows.length,
      cellCount: cells.length,
      rootChildren,
      bodyTextLen: (document.body.innerText || '').length,
      bodyTextPreview: bodyText,
    }
  })
}

main().catch(err => {
  console.error('FATAL:', err)
  process.exit(1)
})
