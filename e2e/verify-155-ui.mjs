// #155 UI acceptance (headed): the embedded xlsx image really renders
//   1. Portal batch detail page   (external_customer view)
//   2. internal feedback review page (admin/user view of the same sample)
//
// Only a decoded <img> (naturalWidth > 0) counts as rendered. A present-but-broken
// <img> is a FAIL, because that is exactly what a bad base64 projection looks like.
//
// Usage: node verify-155-ui.mjs <batchId> <extUsername> <extPassword>
// ASCII-only source on purpose (CJK phantom-read hazard on this box).
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))

const [batchId, extUser, extPass] = process.argv.slice(2)
if (!batchId || !extUser || !extPass) {
  throw new Error('usage: node verify-155-ui.mjs <batchId> <extUsername> <extPassword>')
}

let failed = 0
const fail = (m) => { console.log('FAIL ' + m); failed++ }
const pass = (m) => console.log('PASS ' + m)

async function loginState(username, password) {
  const r = await fetch(BASE + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const tok = await r.json()
  if (!tok.access_token) throw new Error('login failed for ' + username)
  const me = await (await fetch(BASE + '/api/auth/me', {
    headers: { Authorization: 'Bearer ' + tok.access_token },
  })).json()
  const persisted = {
    state: { accessToken: tok.access_token, refreshToken: tok.refresh_token, user: me },
    version: 0,
  }
  return {
    role: me.role,
    storageState: {
      cookies: [],
      origins: [{
        origin: new URL(BASE).origin,
        localStorage: [{ name: 'agent-eval-auth', value: JSON.stringify(persisted) }],
      }],
    },
  }
}

/** An <img> only counts if the browser actually decoded it. */
async function assertDecodedImg(scope, label) {
  const img = scope.locator('img').first()
  try {
    await img.waitFor({ state: 'visible', timeout: 15_000 })
  } catch {
    fail(label + ': no visible <img> at all')
    return 0
  }
  const w = await img.evaluate(el => el.naturalWidth)
  const src = (await img.getAttribute('src')) || ''
  if (w > 0) pass(label + ': image decoded naturalWidth=' + w + ' src=' + src.slice(0, 32) + '...')
  else fail(label + ': <img> present but naturalWidth=0 (broken base64) src=' + src.slice(0, 64))
  return w
}

const browser = await chromium.launch({ headless: false })

try {
  // ================= 1. Portal side (external_customer) =================
  console.log('\n=== 1. Portal batch detail (external_customer) ===')
  const ext = await loginState(extUser, extPass)
  console.log('  ext role=' + ext.role)
  const ctxExt = await browser.newContext({
    baseURL: BASE, storageState: ext.storageState, viewport: { width: 1440, height: 960 },
  })
  const p1 = await ctxExt.newPage()
  p1.on('console', m => { if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 200)) })

  await p1.goto('/portal/batches/' + batchId, { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (p1.url().includes('/login')) throw new Error('portal: bounced to /login (token bad)')
  await p1.waitForTimeout(3000)

  const bodyText = await p1.locator('body').innerText()
  if (/内嵌图导入/.test(bodyText)) pass('P1 portal detail loaded the uploaded samples')
  else fail('P1 portal detail shows no uploaded sample text: ' + JSON.stringify(bodyText.slice(0, 300)))

  // the nav list should mark the image-bearing sample
  if (/\[图片\]|图片/.test(bodyText)) pass('P2 portal page carries an image marker/placeholder')
  else fail('P2 portal page shows no image marker')

  await assertDecodedImg(p1, 'P3 portal detail question block')
  await p1.screenshot({ path: path.join(HERE, 'shot-155-portal-detail.png'), fullPage: true })

  // the plain-text sample must still render, with no image
  const imgCount = await p1.locator('img').count()
  console.log('  portal <img> count on page = ' + imgCount)

  // ---- seed one feedback so the batch reaches the review page ------------
  // /api/feedback/batches inner-joins the feedback aggregate, so an unrated
  // batch is invisible there. Rate the image sample as the external customer.
  const seeded = await p1.evaluate(async (bid) => {
    const raw = localStorage.getItem('agent-eval-auth')
    const tok = raw ? (JSON.parse(raw).state?.accessToken ?? '') : ''
    const h = { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' }
    const list = await (await fetch('/api/portal/batches/' + bid + '/samples?page=1&page_size=50', { headers: h })).json()
    const items = list.items ?? list.samples ?? []
    const target = items.find(s => Array.isArray(s.question_content)) ?? items[0]
    if (!target) return { ok: false, why: 'no samples' }
    const r = await fetch('/api/portal/samples/' + target.id + '/feedback', {
      method: 'POST', headers: h,
      body: JSON.stringify({ overall: 4, scores: {}, comment: 'acceptance #155' }),
    })
    return { ok: r.ok, status: r.status, sampleId: target.id }
  }, batchId)
  if (seeded.ok) pass('P4 seeded one feedback on the image sample (' + seeded.sampleId + ')')
  else fail('P4 could not seed feedback: ' + JSON.stringify(seeded))

  // ================= 2. internal feedback review =================
  console.log('\n=== 2. internal feedback review page ===')
  const probe = JSON.parse(fs.readFileSync(path.join(HERE, 'probe-user.json'), 'utf8'))
  const inner = await loginState(probe.username, probe.password)
  console.log('  internal role=' + inner.role)
  const ctxIn = await browser.newContext({
    baseURL: BASE, storageState: inner.storageState, viewport: { width: 1440, height: 960 },
  })
  const p2 = await ctxIn.newPage()
  p2.on('console', m => { if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 200)) })

  await p2.goto('/feedback', { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (p2.url().includes('/login')) throw new Error('review: bounced to /login')
  await p2.waitForTimeout(3000)
  await p2.screenshot({ path: path.join(HERE, 'shot-155-review-entry.png'), fullPage: true })

  // The batch table inner-joins the feedback subquery, so a batch with zero
  // feedback never shows up. Pick the batch row by its uploaded filename.
  // The upload renames the file to portal-embedded-<stamp>.xlsx, so match that
  // prefix rather than the local fixture filename.
  const batchRow = p2.locator('tr', { hasText: 'portal-embedded-' }).first()
  if (!(await batchRow.isVisible().catch(() => false))) {
    fail('R1 uploaded batch not listed on the review page')
  } else {
    pass('R1 uploaded batch is listed on the review page')
    await batchRow.click()
    await p2.waitForTimeout(2500)

    const sampleRow = p2.locator('tr', { hasText: '内嵌图导入-带图' }).first()
    if (!(await sampleRow.isVisible().catch(() => false))) {
      fail('R2 image-bearing sample row not found in the sample table')
    } else {
      const rowText = await sampleRow.innerText()
      if (/图片/.test(rowText)) pass('R2 sample row shows the image marker')
      else fail('R2 sample row lacks the image marker: ' + JSON.stringify(rowText.slice(0, 160)))
      await p2.screenshot({ path: path.join(HERE, 'shot-155-review-samples.png'), fullPage: true })

      await sampleRow.click()
      await p2.waitForTimeout(2500)
      await assertDecodedImg(p2, 'R3 review sample detail')
      await p2.screenshot({ path: path.join(HERE, 'shot-155-review-detail.png'), fullPage: true })
    }
  }

  console.log('\n' + (failed === 0 ? 'VERDICT PASS' : 'VERDICT FAIL (' + failed + ')'))
} catch (e) {
  console.log('ERROR ' + e.message)
  failed++
} finally {
  await browser.close()
  process.exit(failed === 0 ? 0 : 1)
}
