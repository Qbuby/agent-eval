// #213 headed acceptance: saving Portal feedback must stay fast and must not
// refetch the potentially image-heavy samples page after the POST succeeds.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'
const TARGET_BATCH = process.env.BATCH_ID || 'e3292c54-fca9-44c4-bc39-97c7f06e61ed'
const user = JSON.parse(fs.readFileSync(path.join(HERE, 'portal-ext-user.json'), 'utf8'))
const stamp = Date.now()
const expectedComment = 'acceptance-213-' + stamp
const screenshot = path.join(HERE, 'shot-213-portal-feedback.png')

function assert(condition, message) {
  if (!condition) throw new Error(message)
  console.log('PASS ' + message)
}

async function loginState() {
  const login = await fetch(BASE + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(user),
  })
  const tokens = await login.json()
  if (!tokens.access_token) throw new Error('login failed: HTTP ' + login.status)

  const meResponse = await fetch(BASE + '/api/auth/me', {
    headers: { Authorization: 'Bearer ' + tokens.access_token },
  })
  const me = await meResponse.json()
  return {
    cookies: [],
    origins: [{
      origin: new URL(BASE).origin,
      localStorage: [{
        name: 'agent-eval-auth',
        value: JSON.stringify({
          state: {
            accessToken: tokens.access_token,
            refreshToken: tokens.refresh_token,
            user: me,
          },
          version: 0,
        }),
      }],
    }],
  }
}

const browser = await chromium.launch({ headless: false, executablePath: EXEC })
let failed = false
try {
  const context = await browser.newContext({
    baseURL: BASE,
    storageState: await loginState(),
    viewport: { width: 1440, height: 960 },
  })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/portal/batches/' + TARGET_BATCH, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  })
  assert(!page.url().includes('/login'), 'Portal batch page stays authenticated')
  await page.getByRole('heading', { name: '样例评审' }).waitFor({ timeout: 60_000 })
  await page.getByRole('button', { name: '保存反馈' }).waitFor({ state: 'visible', timeout: 60_000 })

  const sampleGetsAfterClick = []
  let postStartedAt = 0
  let postFinishedAt = 0
  page.on('request', request => {
    const url = request.url()
    if (request.method() === 'POST' && /\/api\/portal\/samples\/[^/]+\/feedback$/.test(url)) {
      postStartedAt = performance.now()
    }
    if (postStartedAt > 0 && request.method() === 'GET' && /\/api\/portal\/batches\/[^/]+\/samples(?:\?|$)/.test(url)) {
      sampleGetsAfterClick.push(url)
    }
  })
  page.on('response', response => {
    if (response.request().method() === 'POST' && /\/api\/portal\/samples\/[^/]+\/feedback$/.test(response.url())) {
      postFinishedAt = performance.now()
    }
  })

  const overall = page.getByRole('radiogroup', { name: '总体评分' })
  await overall.getByRole('radio', { name: /^4 分/ }).click()
  await page.getByPlaceholder('对该样例的质量、问题或改进建议（可选）').fill(expectedComment)

  const responsePromise = page.waitForResponse(response =>
    response.request().method() === 'POST'
      && /\/api\/portal\/samples\/[^/]+\/feedback$/.test(response.url()),
  { timeout: 15_000 })
  const wallStartedAt = performance.now()
  await page.getByRole('button', { name: '保存反馈' }).click()
  const response = await responsePromise
  const wallMs = performance.now() - wallStartedAt
  const postMs = postFinishedAt - postStartedAt

  assert(response.ok(), 'feedback POST returns HTTP ' + response.status())
  assert(wallMs < 5000, 'save response completes in ' + Math.round(wallMs) + ' ms (< 5000 ms)')
  assert(postMs >= 0 && postMs < 5000, 'measured POST duration is ' + Math.round(postMs) + ' ms')
  await page.getByText('反馈已保存', { exact: true }).waitFor({ state: 'visible', timeout: 5000 })
  assert(true, 'success toast is visible')

  // Give React Query enough time to expose an accidental invalidate/refetch.
  await page.waitForTimeout(2000)
  assert(sampleGetsAfterClick.length === 0, 'saving does not refetch the samples page')

  await page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 })
  await page.getByPlaceholder('对该样例的质量、问题或改进建议（可选）').waitFor({ timeout: 60_000 })
  const persistedComment = await page.getByPlaceholder('对该样例的质量、问题或改进建议（可选）').inputValue()
  assert(persistedComment === expectedComment, 'saved feedback persists after a full page reload')
  assert(await overall.getByRole('radio', { name: /^4 分/ }).isChecked(), 'saved overall score persists after reload')
  assert(consoleErrors.length === 0, 'browser console has no errors')

  await page.screenshot({ path: screenshot, fullPage: true })
  console.log('SCREENSHOT ' + screenshot)
  console.log('VERDICT PASS')
} catch (error) {
  failed = true
  console.error('VERDICT FAIL ' + (error?.stack || error))
} finally {
  await browser.close()
}

process.exit(failed ? 1 : 0)
