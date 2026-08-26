// Verify the session-creation race fix on /omniagent (task #212).
//
// What is under test: clicking "new session" and hitting Enter before that POST
// returns used to fire TWO POST /omniagent/sessions and drop the optimistic
// bubbles. The fix is a single-flight create + keeping optimistic messages by
// session_id instead of an all-or-nothing skip flag.
//
// The race is made deterministic by delaying the create POST via route(), so
// this does not depend on how fast the operator can type.
//
// ASCII-only source on purpose (CJK phantom-read box) -> CJK selectors are \u escapes.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const STORAGE = path.join(HERE, 'auth.json')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'

// CJK selectors / copy
const NEW = '\u65b0\u5efa'                                     // new
const INPUT_LABEL = '\u6d88\u606f\u8f93\u5165\u6846'           // message input
const SEND = '\u53d1\u9001'                                    // send
const HEADING = 'OmniAgent \u5bf9\u8bdd'                       // OmniAgent chat
const NAV = '\u7cfb\u7edf\u667a\u80fd\u4f53'                   // system agent
const ONLY_REPLY = '\u53ea\u56de\u590d\uff1a'                  // "reply only:"

const ANSWER_TIMEOUT = 180_000

// Token out of the same storageState the browser uses, for API-side counting.
const storage = JSON.parse(fs.readFileSync(STORAGE, 'utf8'))
const lsEntry = storage.origins?.[0]?.localStorage?.find(e => e.name === 'agent-eval-auth')
if (!lsEntry) throw new Error('auth.json has no agent-eval-auth entry')
const TOKEN = JSON.parse(lsEntry.value).state.accessToken
if (!TOKEN) throw new Error('auth.json has no accessToken')

async function countSessions() {
  const r = await fetch(`${BASE}/api/omniagent/sessions?page=1&page_size=200`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  })
  if (!r.ok) throw new Error(`GET /api/omniagent/sessions -> ${r.status}`)
  const body = await r.json()
  if (typeof body.total !== 'number') throw new Error('sessions page has no numeric total')
  return body.total
}

const fails = []
function check(name, ok, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`)
  if (!ok) fails.push(name)
}

const browser = await chromium.launch({ headless: false, executablePath: EXEC })
const context = await browser.newContext({
  baseURL: BASE,
  storageState: STORAGE,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

// Count create POSTs. Reset per phase.
let createPosts = 0
page.on('request', req => {
  if (req.method() === 'POST' && /\/api\/omniagent\/sessions$/.test(req.url())) createPosts++
})

const suffix = Date.now().toString(36)
const firstPrompt = `${ONLY_REPLY}OA_BASE_${suffix}`
const firstAnswer = `OA_BASE_${suffix}`
const racePrompt = `${ONLY_REPLY}OA_RACE_${suffix}`
const raceAnswer = `OA_RACE_${suffix}`

async function waitAnswer(answer) {
  await page.getByText(answer, { exact: true }).waitFor({ state: 'visible', timeout: ANSWER_TIMEOUT })
  // stream finished -> the send button is back (it is replaced by "stop" mid-stream)
  await page.getByRole('button', { name: SEND, exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
}

try {
  await page.goto('/omniagent')
  await page.waitForLoadState('networkidle')
  if (page.url().includes('/login')) throw new Error('storageState expired, page bounced to /login')
  await page.getByRole('link', { name: NAV }).waitFor({ state: 'visible', timeout: 20_000 })
  await page.getByRole('heading', { name: HEADING }).waitFor({ state: 'visible' })

  // ── Phase A: a normal session with a real answer, so the race phase has a
  // non-empty previous session to (incorrectly) leak from.
  const newBtn = page.getByRole('button', { name: NEW, exact: true })
  await newBtn.click()
  const input = page.getByRole('textbox', { name: INPUT_LABEL })
  await input.fill(firstPrompt)
  await input.press('Enter')
  await waitAnswer(firstAnswer)
  console.log('phase A ready, baseline answer visible')

  const beforeRace = await countSessions()

  // ── Phase B: hold the create POST behind a gate. This proves the DOM sample is
  // taken while the request is definitely in flight, not just near that window.
  let releaseCreate
  let markCreateSeen
  const createGate = new Promise(resolve => { releaseCreate = resolve })
  const createSeen = new Promise(resolve => { markCreateSeen = resolve })
  await page.route('**/api/omniagent/sessions', async route => {
    if (route.request().method() !== 'POST') return route.fallback()
    markCreateSeen()
    await createGate
    return route.fallback()
  })

  createPosts = 0
  await newBtn.evaluate(button => button.click())
  await Promise.race([
    createSeen,
    new Promise((_, reject) => setTimeout(() => reject(new Error('create POST did not reach route gate')), 5000)),
  ])

  // Inside the gate: inspect native buttons directly. During loading the spinner
  // changes the accessibility tree, so a role locator by its old name can be empty
  // even though the same DOM button is present and disabled.
  const allButtonDom = await page.locator('button').evaluateAll((buttons, newLabel) =>
    buttons.map(button => ({
      text: button.textContent?.trim(),
      ariaLabel: button.getAttribute('aria-label'),
      disabled: button.disabled,
      ariaBusy: button.getAttribute('aria-busy'),
      visible: !!(button.offsetWidth || button.offsetHeight || button.getClientRects().length),
      isNew: button.textContent?.trim() === newLabel,
    })),
  NEW)
  const newButtonDom = allButtonDom.filter(button => button.isNew)
  console.log('CREATE_GATE_DOM=' + JSON.stringify(newButtonDom))
  const visibleNewButton = newButtonDom.find(button => button.visible)
  const disabledDuringCreate = !!visibleNewButton?.disabled && visibleNewButton.ariaBusy === 'true'
  check('new-session button disabled while creating', disabledDuringCreate)
  const inputEnabled = await input.isEnabled()
  check('input still enabled while creating', inputEnabled)

  // Send while create is still gated -> must join the same Promise. Release only
  // after the second entry has had a chance to observe the in-flight create.
  await input.fill(racePrompt)
  await input.press('Enter')
  releaseCreate()

  // The user's own words appears as soon as create resolves, before the answer.
  await page.getByText(racePrompt, { exact: true }).waitFor({ state: 'visible', timeout: 15_000 })
  check('optimistic user bubble survives the create', true)

  await waitAnswer(raceAnswer)

  check('exactly one create POST for the race', createPosts === 1, `createPosts=${createPosts}`)

  const afterRace = await countSessions()
  check('exactly one new session on the server', afterRace === beforeRace + 1,
    `before=${beforeRace} after=${afterRace}`)

  const leaked = await page.getByText(firstAnswer, { exact: true }).isVisible().catch(() => false)
  check('previous session answer did not leak into the new session', !leaked)

  const raceVisible = await page.getByText(raceAnswer, { exact: true }).isVisible()
  check('race answer visible in the active session', raceVisible)

  // Reload proves it was persisted to the session that is actually selected,
  // not just held in local state.
  await page.reload()
  await page.waitForLoadState('networkidle')
  const afterReload = await page.getByText(raceAnswer, { exact: true })
    .isVisible({ timeout: 20_000 }).catch(() => false)
  check('race answer still there after reload', afterReload)

  console.log(fails.length === 0 ? 'RESULT=PASS' : `RESULT=FAIL count=${fails.length} ${fails.join(' | ')}`)
} catch (err) {
  console.log('RESULT=ERROR', err && err.message ? err.message : String(err))
  process.exitCode = 1
} finally {
  await page.screenshot({ path: path.join(HERE, `shot-212-create-race-${suffix}.png`), fullPage: true }).catch(() => {})
  await browser.close()
}
if (fails.length) process.exitCode = 1
