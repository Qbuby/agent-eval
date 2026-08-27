import { chromium, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../..')
const BASE = process.env.OA_TWO_TENANT_URL || 'http://127.0.0.1:18082'
const EVIDENCE_DIR = path.join(ROOT, '.codex_tmp/omniagent-two-tenant-evidence')
const FIXTURE = JSON.parse(
  fs.readFileSync(path.join(ROOT, '.codex_tmp/omniagent-two-tenant-fixture.json'), 'utf8'),
)
const EXEC = process.env.PW_CHROMIUM
  || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'

const browser = await chromium.launch({ headless: true, executablePath: EXEC })
fs.mkdirSync(EVIDENCE_DIR, { recursive: true })

async function login(user) {
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
    reducedMotion: 'reduce',
  })
  const page = await context.newPage()
  await page.goto('/login')
  await page.getByPlaceholder('输入用户名').fill(user.username)
  const password = page.getByPlaceholder('输入密码')
  await password.fill(user.password)
  await page.getByRole('button', { name: /继续|登录/ }).waitFor({ state: 'visible' })
  await password.press('Enter')
  await page.waitForURL(/\/dashboard/, { timeout: 20_000 })
  return { context, page }
}

async function authenticatedFetch(page, url, init = {}) {
  return page.evaluate(async ({ url, init }) => {
    const raw = localStorage.getItem('agent-eval-auth')
    const token = raw ? JSON.parse(raw)?.state?.accessToken : null
    const headers = { ...(init.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
    const response = await fetch(url, { ...init, headers })
    const contentType = response.headers.get('content-type') || ''
    const body = contentType.includes('application/json') ? await response.json() : await response.text()
    return { status: response.status, body }
  }, { url, init })
}

async function verifyTenant(key, otherKey) {
  const own = FIXTURE[key]
  const other = FIXTURE[otherKey]
  const { context, page } = await login(own)
  try {
    await page.goto('/omniagent')
    await page.getByRole('heading', { name: 'OmniAgent 对话' }).waitFor({ state: 'visible' })
    await expect(
      page.getByRole('button', { name: new RegExp(`^${own.marker} Session \\d+ 条`) }),
    ).toBeVisible()
    await expect(
      page.getByRole('button', { name: new RegExp(`^${other.marker} Session \\d+ 条`) }),
    ).toHaveCount(0)
    await expect(page.getByText(`analysis.${own.marker.toLowerCase()}`, { exact: true })).toBeVisible()
    await expect(page.getByText(`${own.marker}.event`, { exact: true })).toBeVisible()
    await expect(page.getByText(other.marker, { exact: false })).toHaveCount(0)

    await page.getByRole('tab', { name: '审批' }).click({ force: true })
    await expect(page.getByText(`${own.marker} Approval`, { exact: true })).toBeVisible()
    await expect(page.getByText(other.marker, { exact: false })).toHaveCount(0)

    await page.getByRole('tab', { name: '制品' }).click({ force: true })
    await expect(page.getByText(`${own.marker}.txt`, { exact: true })).toBeVisible()
    await expect(page.getByText(`${other.marker}.txt`, { exact: true })).toHaveCount(0)

    await page.getByRole('tab', { name: '记忆' }).click({ force: true })
    await expect(page.getByText(`${own.marker} Memory`, { exact: true })).toBeVisible()
    await expect(page.getByText(`${other.marker} Memory`, { exact: true })).toHaveCount(0)

    await page.getByRole('tab', { name: '通知' }).click({ force: true })
    await expect(page.getByText(`${own.marker} Notification`, { exact: true })).toBeVisible()
    await expect(page.getByText(`${other.marker} Notification`, { exact: true })).toHaveCount(0)

    await page.getByRole('tab', { name: '计划' }).click({ force: true })
    await expect(page.getByText(`${own.marker} Schedule`, { exact: true })).toBeVisible()
    await expect(page.getByText(`${other.marker} Schedule`, { exact: true })).toHaveCount(0)
    await page.screenshot({
      path: path.join(EVIDENCE_DIR, `${key}-work-panel.png`),
      animations: 'disabled',
      fullPage: false,
      timeout: 120_000,
    })

    const listChecks = await Promise.all([
      ['/api/omniagent/jobs', own.job_id, other.job_id],
      ['/api/omniagent/actions', own.action_id, other.action_id],
      ['/api/omniagent/artifacts', own.artifact_id, other.artifact_id],
      ['/api/omniagent/memories', own.memory_id, other.memory_id],
      ['/api/omniagent/notifications', own.notification_id, other.notification_id],
      ['/api/omniagent/schedules', own.schedule_id, other.schedule_id],
      ['/api/omniagent/events', own.marker, other.marker],
    ].map(async ([url, ownEvidence, otherEvidence]) => ({
      url,
      ownEvidence,
      otherEvidence,
      result: await authenticatedFetch(page, url),
    })))
    for (const { url, ownEvidence, otherEvidence, result } of listChecks) {
      if (result.status !== 200) throw new Error(`list endpoint returned ${result.status}`)
      const serialized = JSON.stringify(result.body)
      if (!serialized.includes(ownEvidence)) {
        throw new Error(`own evidence absent from ${url}: ${serialized}`)
      }
      if (serialized.includes(otherEvidence)) {
        throw new Error(`cross-tenant evidence leaked from ${url}: ${serialized}`)
      }
    }

    const directChecks = await Promise.all([
      authenticatedFetch(page, `/api/omniagent/sessions/${other.session_id}`),
      authenticatedFetch(page, `/api/omniagent/jobs/${other.job_id}`),
      authenticatedFetch(page, `/api/omniagent/actions/${other.action_id}`),
      authenticatedFetch(page, `/api/omniagent/artifacts/${other.artifact_id}/download`),
    ])
    for (const result of directChecks) {
      if (result.status !== 404) throw new Error(`cross-tenant direct access returned ${result.status}`)
    }

    const mutationChecks = await Promise.all([
      authenticatedFetch(page, `/api/omniagent/sessions/${other.session_id}`, { method: 'DELETE' }),
      authenticatedFetch(page, `/api/omniagent/memories/${other.memory_id}`, { method: 'DELETE' }),
      authenticatedFetch(page, `/api/omniagent/jobs/${other.job_id}/cancel`, { method: 'POST' }),
      authenticatedFetch(page, `/api/omniagent/notifications/${other.notification_id}/read`, { method: 'POST' }),
      authenticatedFetch(page, `/api/omniagent/schedules/${other.schedule_id}/pause`, { method: 'POST' }),
    ])
    for (const result of mutationChecks) {
      if (result.status !== 404) throw new Error(`cross-tenant mutation returned ${result.status}`)
    }

    const memoryQuery = await authenticatedFetch(
      page,
      `/api/omniagent/memories?q=${encodeURIComponent(other.marker)}`,
    )
    if (memoryQuery.status !== 200 || JSON.stringify(memoryQuery.body).includes(other.marker)) {
      throw new Error('cross-tenant memory query leaked data')
    }

    const eventCursor = await authenticatedFetch(
      page,
      `/api/omniagent/events?after=${Math.max(0, other.event_cursor - 1)}`,
    )
    if (eventCursor.status !== 200 || JSON.stringify(eventCursor.body).includes(other.marker)) {
      throw new Error('cross-tenant event cursor leaked data')
    }

    const eventSession = await authenticatedFetch(
      page,
      `/api/omniagent/events?session_id=${other.session_id}`,
    )
    if (eventSession.status !== 200 || eventSession.body?.items?.length !== 0) {
      throw new Error('cross-tenant event session filter leaked data')
    }

    return {
      tenant: key,
      marker: own.marker,
      ui: ['session', 'activity', 'approval', 'artifact', 'memory', 'notification', 'schedule'],
      listIsolation: true,
      directAccess404: true,
      mutation404: true,
      queryIsolation: true,
      cursorIsolation: true,
    }
  } finally {
    await context.close()
  }
}

try {
  const alpha = await verifyTenant('a', 'b')
  const beta = await verifyTenant('b', 'a')
  // Revisit both owners after all cross-tenant mutation attempts. This proves
  // the 404 responses were non-destructive rather than partial side effects.
  const alphaAfterMutations = await verifyTenant('a', 'b')
  const betaAfterMutations = await verifyTenant('b', 'a')
  const result = { ok: true, alpha, beta }
  result.postMutationOwnership = {
    alpha: alphaAfterMutations.listIsolation,
    beta: betaAfterMutations.listIsolation,
  }
  const rendered = JSON.stringify(result, null, 2)
  fs.writeFileSync(path.join(EVIDENCE_DIR, 'browser-result.json'), `${rendered}\n`)
  console.log(rendered)
} finally {
  await browser.close()
}
