// #158 headed acceptance: rescore failure reasons must be visible in the UI.
// Fixture run 5238a8b6 has two deliberately broken judge providers:
//   - one returns HTTP 405  -> classified "config"    (retry can never help)
//   - one is unreachable    -> classified "transient" (retry may help)
// So the completed banner must show BOTH the config-class guidance sentence
// (change the provider first) and the per-dimension reason list, and must NOT
// show the old misleading single line ("upstream judge still hasn't scored").
// The expected-copy needles below must be literal CJK to match the DOM, so this
// file is NOT ASCII-only: verify its bytes with node, never with Read.
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const AUTH = path.join(HERE, 'auth.json')
const SHOT = path.join(HERE, 'shot-158-rescore-reason.png')
const OUT = path.join(HERE, 'result-158-rescore-reason.json')
const BASE = process.env.BASE_URL || 'http://localhost'
const RUN_ID = process.env.RUN_ID || '5238a8b6-d75c-447f-84e0-4543353e8f71'

// Expected UI copy, escaped so this file stays ASCII on disk.
const T = {
  btn: '补评缺分维度',                       // rescore button
  summary: '已补评：扫描',                   // "scored: scanned"
  stillMissing: '仍缺',                                      // "still missing"
  rejected: '上游 judge 拒收了本次输入',
  changeProvider: '需先把该评估器换成'
    + '支持这类输入的 provider / 模型',
  kindConfig: '（配置）',                            // "(config)"
  kindTransient: '（暂时性）',                   // "(transient)"
  oldMisleading: '上游 judge 仍未出分',       // must be absent
}

const auth = JSON.parse(fs.readFileSync(AUTH, 'utf8'))
const persisted = auth.origins
  ?.find(o => o.origin === new URL(BASE).origin)
  ?.localStorage?.find(x => x.name === 'agent-eval-auth')?.value
if (!persisted) throw new Error('auth.json has no agent-eval-auth entry')
const token = JSON.parse(persisted).state.accessToken

const evidence = { run_id: RUN_ID, screenshot: SHOT, checks: {} }
const fail = []
let browser = null

try {
  browser = await chromium.launch({ headless: false, slowMo: 60 })
  const context = await browser.newContext({
    baseURL: BASE,
    storageState: AUTH,
    viewport: { width: 1440, height: 1000 },
  })
  const page = await context.newPage()

  // Record what the poller actually got back, so the DOM assertions can be
  // cross-checked against the API payload rather than trusted on their own.
  const statusPayloads = []
  page.on('response', async res => {
    if (!res.url().includes('/rescore-status')) return
    try { statusPayloads.push(await res.json()) } catch { /* non-JSON */ }
  })

  await page.goto(`/evaluation/runs/${RUN_ID}`, { waitUntil: 'networkidle' })

  const btn = page.getByRole('button', { name: T.btn })
  await btn.waitFor({ state: 'visible', timeout: 30_000 })
  evidence.checks.button_visible = true
  await btn.click()

  // Poll until the job reaches a terminal state (the banner only renders then).
  const deadline = Date.now() + 180_000
  let terminal = null
  while (Date.now() < deadline) {
    const r = await fetch(`${BASE}/api/eval/runs/${RUN_ID}/rescore-status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const body = await r.json()
    if (body.status === 'completed' || body.status === 'error') { terminal = body; break }
    await page.waitForTimeout(2000)
  }
  if (!terminal) throw new Error('rescore did not reach a terminal state in 180s')
  evidence.api_terminal = terminal
  if (terminal.status !== 'completed') fail.push(`api status=${terminal.status}`)
  if ((terminal.failures_config ?? 0) < 1) fail.push('api failures_config < 1')
  if ((terminal.failures_transient ?? 0) < 1) fail.push('api failures_transient < 1')
  if (!Array.isArray(terminal.failures) || terminal.failures.length < 2) {
    fail.push('api failures[] shorter than 2')
  }

  // Let the UI's own poll land the completed banner.
  await page.getByText(T.summary, { exact: false }).first()
    .waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForTimeout(800)

  const bodyText = await page.locator('body').innerText()
  evidence.status_payloads = statusPayloads.slice(-3)

  const want = [
    ['summary_line', T.summary],
    ['still_missing', T.stillMissing],
    ['config_rejected_wording', T.rejected],
    ['config_change_provider_wording', T.changeProvider],
    ['kind_label_config', T.kindConfig],
    ['kind_label_transient', T.kindTransient],
  ]
  for (const [key, needle] of want) {
    const ok = bodyText.includes(needle)
    evidence.checks[key] = ok
    if (!ok) fail.push(`missing in DOM: ${key}`)
  }

  const oldGone = !bodyText.includes(T.oldMisleading)
  evidence.checks.old_misleading_text_absent = oldGone
  if (!oldGone) fail.push('old misleading line still rendered')

  // Every dimension name the API reported should appear in the rendered list.
  const dims = (terminal.failures || []).map(f => f.dimension)
  evidence.rendered_dimensions = dims.map(d => ({ dimension: d, in_dom: bodyText.includes(d) }))
  for (const d of dims) {
    if (!bodyText.includes(d)) fail.push(`dimension not rendered: ${d}`)
  }

  // And the reason text itself, not just the label.
  const reasons = (terminal.failures || []).map(f => (f.error || '').slice(0, 40))
  evidence.rendered_reasons = reasons.map(r => ({ head: r, in_dom: bodyText.includes(r) }))
  for (const r of reasons) {
    if (r && !bodyText.includes(r)) fail.push(`reason not rendered: ${r}`)
  }

  await page.screenshot({ path: SHOT, fullPage: true })
} finally {
  if (browser) await browser.close()
}

evidence.failures = fail
fs.writeFileSync(OUT, JSON.stringify(evidence, null, 2), 'utf8')
console.log(JSON.stringify(evidence, null, 2))
if (fail.length) {
  console.log('VERIFY_158_UI_FAIL')
  process.exit(1)
}
console.log('VERIFY_158_UI_OK')
