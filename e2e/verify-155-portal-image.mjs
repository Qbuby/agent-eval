// #155 acceptance: Portal samples carry embedded xlsx images end to end.
//
// Path under test:
//   POST /api/portal/batches/upload  (external_customer)  -> row_images_for_upload
//     -> dual write: question (text projection with [image] placeholder)
//                  + question_content (canonical blocks) only when images exist
//   GET  /api/portal/batches/{id}/samples                  -> SampleItem.question_content
//   GET  /api/feedback-review/... (internal user)          -> same blocks on the admin side
//
// The xlsx fixture anchors one picture at 0-based drawing row 1 = Excel row 2,
// so sample #1 must carry an image and sample #2 (Excel row 3) must not.
// That asymmetry is the real test: it proves excel_row mapping does not drift.
//
// ASCII-only source on purpose (CJK phantom-read hazard on this box).
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.BASE_URL || 'http://localhost'
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const XLSX = path.join(HERE, '_fixture_153_embedded.xlsx')
const ENTRY_CODE = process.env.ENTRY_CODE || 'ExtT2026!'
const OUT = path.join(HERE, 'portal-ext-user.json')

if (!fs.existsSync(XLSX)) throw new Error('missing fixture ' + XLSX)

let failed = 0
const fail = (m) => { console.log('FAIL ' + m); failed++ }
const pass = (m) => console.log('PASS ' + m)

async function jpost(url, body, tok) {
  const h = { 'Content-Type': 'application/json' }
  if (tok) h.Authorization = 'Bearer ' + tok
  const r = await fetch(BASE + url, { method: 'POST', headers: h, body: JSON.stringify(body) })
  const t = await r.text()
  return { status: r.status, body: t ? JSON.parse(t) : null, raw: t }
}
async function jget(url, tok) {
  const r = await fetch(BASE + url, { headers: { Authorization: 'Bearer ' + tok } })
  const t = await r.text()
  return { status: r.status, body: t ? JSON.parse(t) : null, raw: t }
}

// ---- 1. external_customer account in the DEFAULT tenant -------------------
const stamp = Date.now()
const ext = { username: 'portalext_' + stamp, password: 'Portal!' + stamp }
let reg = await jpost('/api/auth/register', {
  username: ext.username,
  password: ext.password,
  email: 'portalext_' + stamp + '@example.com',
  entry_code: ENTRY_CODE,
})
if (reg.status !== 201) throw new Error('register -> ' + reg.status + ' ' + reg.raw.slice(0, 300))
if (reg.body.role !== 'external_customer') fail('registered role=' + reg.body.role + ', want external_customer')
else pass('registered external_customer ' + ext.username)
fs.writeFileSync(OUT, JSON.stringify(ext, null, 2), 'utf8')

const extLogin = await jpost('/api/auth/login', ext)
if (!extLogin.body?.access_token) throw new Error('ext login failed ' + extLogin.raw.slice(0, 200))
const extTok = extLogin.body.access_token

// ---- 2. upload the xlsx with the embedded picture -------------------------
const fd = new FormData()
fd.append('file', new Blob([fs.readFileSync(XLSX)], {
  type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}), 'portal-embedded-' + stamp + '.xlsx')
const upRes = await fetch(BASE + '/api/portal/batches/upload', {
  method: 'POST',
  headers: { Authorization: 'Bearer ' + extTok },
  body: fd,
})
const upTxt = await upRes.text()
if (upRes.status !== 200) throw new Error('upload -> ' + upRes.status + ' ' + upTxt.slice(0, 400))
const up = JSON.parse(upTxt)
console.log('upload ok: rows=' + up.row_count + ' with_images=' + up.with_images + ' batch=' + up.batch?.id)
if (up.with_images === 1) pass('upload reports with_images=1 (only the anchored row)')
else fail('upload with_images=' + JSON.stringify(up.with_images) + ', want 1')
const batchId = up.batch?.id
if (!batchId) throw new Error('no batch id in upload response')

// ---- 3. portal samples API carries canonical blocks ----------------------
const sp = await jget('/api/portal/batches/' + batchId + '/samples?page=1&page_size=50', extTok)
if (sp.status !== 200) throw new Error('samples -> ' + sp.status + ' ' + sp.raw.slice(0, 300))
const items = sp.body.items ?? []
console.log('samples returned: ' + items.length)

const withImg = items.filter(s => Array.isArray(s.question_content))
const withoutImg = items.filter(s => s.question_content == null)
if (withImg.length === 1) pass('exactly 1 sample has question_content blocks')
else fail(withImg.length + ' samples have question_content, want 1')
if (withoutImg.length === items.length - withImg.length) pass('remaining samples keep question_content=null')

const imgSample = withImg[0]
if (imgSample) {
  const blocks = imgSample.question_content
  const img = blocks.find(b => b?.type === 'image')
  const txt = blocks.find(b => b?.type === 'text')
  if (!img) fail('no image block: ' + JSON.stringify(blocks).slice(0, 200))
  else if (img.source?.type !== 'base64') fail('image.source.type=' + img.source?.type)
  else if (!img.source?.data || img.source.data.length < 100) fail('image data too short: ' + (img.source?.data || '').length)
  else pass('image block is base64 ' + img.source.media_type + ', data len=' + img.source.data.length)
  if (txt && /内嵌图导入/.test(txt.text)) pass('text block preserved the question text')
  else fail('text block missing/unexpected: ' + JSON.stringify(txt).slice(0, 160))
  // question stays a plain-text projection with the placeholder
  if (/\[图片\]/.test(imgSample.question || '')) pass('question is a text projection containing the image placeholder')
  else fail('question lacks placeholder: ' + JSON.stringify(imgSample.question).slice(0, 160))
  // and it must be the FIRST data row (Excel row 2), not a drifted one
  const idx = items.indexOf(imgSample)
  if (idx === 0) pass('the image landed on the first data row (Excel row 2) - no row drift')
  else fail('image landed on list index ' + idx + ', want 0 (row drift)')
}

// ---- 4. internal feedback-review sees the same blocks -------------------
const probe = JSON.parse(fs.readFileSync(path.join(HERE, 'probe-user.json'), 'utf8'))
const inLogin = await jpost('/api/auth/login', { username: probe.username, password: probe.password })
const inTok = inLogin.body?.access_token
if (!inTok) fail('internal login failed, skipping review-side checks')
else {
  const rs = await jget('/api/feedback/batches/' + batchId + '/samples?page=1&page_size=50', inTok)
  if (rs.status !== 200) {
    fail('feedback-review samples -> ' + rs.status + ' ' + rs.raw.slice(0, 300))
  } else {
    const rows = rs.body.items ?? rs.body.samples ?? []
    const rWith = rows.filter(s => Array.isArray(s.question_content))
    if (rWith.length === 1 && rWith[0].question_content.some(b => b?.type === 'image')) {
      pass('feedback-review row carries the same image block')
    } else {
      fail('feedback-review rows: ' + rows.length + ', with blocks: ' + rWith.length)
    }
    const sid = rWith[0]?.id ?? rows[0]?.id
    if (sid) {
      const det = await jget('/api/feedback/samples/' + sid, inTok)
      if (det.status !== 200) fail('review detail -> ' + det.status + ' ' + det.raw.slice(0, 200))
      else if (Array.isArray(det.body.question_content) && det.body.question_content.some(b => b?.type === 'image')) {
        pass('feedback-review detail carries the image block')
      } else {
        fail('review detail question_content=' + JSON.stringify(det.body.question_content).slice(0, 160))
      }
    }
  }
}

console.log('\nBATCH_ID=' + batchId)
console.log('EXT_USER=' + ext.username)
console.log(failed === 0 ? 'VERDICT PASS' : 'VERDICT FAIL (' + failed + ')')
process.exit(failed === 0 ? 0 : 1)
