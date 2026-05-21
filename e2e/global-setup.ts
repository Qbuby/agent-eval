/**
 * Globally registers a fresh user once and saves the auth storage state to
 * `auth.json` so every spec can opt in by setting `test.use({ storageState })`.
 *
 * Generates a unique username/email per run so we never collide across runs.
 */
import { chromium, request, type FullConfig } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'

const TIMESTAMP = Date.now()
export const TEST_USER = {
  username: `e2e_${TIMESTAMP}`,
  email: `e2e_${TIMESTAMP}@example.com`,
  password: 'Password123!',
}

const STORAGE_FILE = path.resolve(__dirname, 'auth.json')
// The HTTP-reachable URL we use to *probe* the agent. Tests inside docker
// hit the same agent via host.docker.internal — but the probe is on the
// host so we can verify before launching the suite.
const AGENT_PROBE_URL = process.env.AGENT_PROBE_URL || 'http://localhost:18096/api/agent/langgraph'

async function probeAgent(): Promise<void> {
  const ctx = await request.newContext({ timeout: 15_000 })
  let lastErr = ''
  for (let i = 1; i <= 3; i++) {
    try {
      const r = await ctx.post(AGENT_PROBE_URL, {
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        data: {
          question: 'ping',
          configurable: { thread_id: `e2e-probe-${i}`, language: '请用中文回复' },
          stream: true,
        },
      })
      if (r.ok()) {
        await ctx.dispose()
        // eslint-disable-next-line no-console
        console.log(`[globalSetup] agent reachable at ${AGENT_PROBE_URL}`)
        return
      }
      lastErr = `HTTP ${r.status()}`
    } catch (e) {
      lastErr = String(e).slice(0, 200)
    }
    await new Promise(res => setTimeout(res, 2000))
  }
  await ctx.dispose()
  throw new Error(
    `Agent probe failed at ${AGENT_PROBE_URL} after 3 attempts (last: ${lastErr}). ` +
    `Refusing to run the e2e suite — passing tests against a dead agent would be a false positive. ` +
    `Either start the agent and re-run, or set AGENT_PROBE_URL to a reachable endpoint.`,
  )
}

export default async function globalSetup(config: FullConfig) {
  // 1. Probe agent first — fail loud if not up
  // Skip when SKIP_AGENT_PROBE=1 — used for tests that don't drive a real
  // run (e.g. UI-only banner / DOM contract tests).
  if (process.env.SKIP_AGENT_PROBE !== '1') {
    await probeAgent()
  } else {
    // eslint-disable-next-line no-console
    console.log('[globalSetup] SKIP_AGENT_PROBE=1 — skipping agent probe')
  }

  // 2. Persist test user info
  fs.writeFileSync(
    path.resolve(__dirname, 'test-user.json'),
    JSON.stringify(TEST_USER),
  )

  const baseURL = config.projects[0].use?.baseURL || 'http://localhost'
  const browser = await chromium.launch({ headless: true })
  const ctx = await browser.newContext({ baseURL })
  const page = await ctx.newPage()

  await page.goto('/register')
  await page.getByPlaceholder('选择用户名').fill(TEST_USER.username)
  await page.getByPlaceholder('you@example.com').fill(TEST_USER.email)
  await page.getByPlaceholder('至少 8 位，含字母和数字').fill(TEST_USER.password)
  await page.getByPlaceholder('再次输入密码').fill(TEST_USER.password)
  await page.getByRole('button', { name: /注册|创建/ }).click()

  await page.waitForURL(/\/login/, { timeout: 15_000 })

  await page.getByPlaceholder('输入用户名').fill(TEST_USER.username)
  await page.getByPlaceholder('输入密码').fill(TEST_USER.password)
  await page.getByRole('button', { name: '继续' }).click()
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 })

  await ctx.storageState({ path: STORAGE_FILE })
  await browser.close()
}
