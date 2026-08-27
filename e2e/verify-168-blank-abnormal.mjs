// UI acceptance for #168: blank replies must be folded into the "abnormal samples" quick filter.
//
// Targets picked from LIVE data by pick-168-target.mjs. Each one has gap>0 (a blank row whose
// execution status is NORMAL, so the OLD status-only filter provably missed it) AND clean_rows>0
// (exclude-mode leaves a non-empty set), so the three modes render distinguishable counts.
// Between them they cover every branch of rowHasBlankReply():
//   41a47c34  single-turn single-model  -> actual_output blank
//   935665cf  multi-turn single-model   -> some turn's assistant blank (answer_counts absent)
//   f67421b0  multi-turn comparative    -> comparison.answer_counts.b_blank = 4
//   c433dbaa  multi-turn comparative    -> answer_counts null, falls through to per-turn scan
//
// Assertions per run:
//   1. mode=all      -> no filter chip, all rows rendered, blank tags == expected new_abnormal
//   2. mode=only     -> rendered count == new_abnormal, chip == same, every row carries a blank
//                       tag or an abnormal status
//   3. mode=exclude  -> rendered count == clean_rows, zero blank tags
//   4. only + exclude partition the full row set (no overlap, no loss)
//   5. only > old_abnormal, i.e. the filter really did widen (this is the point of #168)
//
// ALL CJK needles are \u escapes on purpose: this repo has a known hazard where tools return
// phantom content for files containing literal CJK. Keep this file pure ASCII.

import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost'
const AUTH = 'D:\\program\\agent_eval\\e2e\\auth.json'
const OUT = 'D:\\program\\agent_eval\\e2e\\result-168-ui.json'

const T = {
  all: '\u4e0d\u7b5b',                        // 不筛
  only: '\u4ec5\u5f02\u5e38',                 // 仅异常
  exclude: '\u6392\u9664\u5f02\u5e38',        // 排除异常
  blankTag: '\u7a7a\u56de\u590d',             // 空回复
  abnormalLabel: '\u5f02\u5e38\u6837\u4f8b',  // 异常样例
  thCase: '\u6837\u4f8b',                     // \u6837\u4f8b   (sample-table header)
  thTrace: '\u8ffd\u8e2a',                    // \u8ffd\u8e2a   (sample-table header)
  filteredPrefix: '\uff08\u7b5b\u51fa',     // （筛出
}

const RUNS = [
  { id: '41a47c34-c526-4126-9ceb-e9f10f43a467', total: 2, oldAbn: 0, newAbn: 1, clean: 1,
    branch: 'single-turn actual_output blank' },
  { id: '935665cf-d720-47e4-a79c-cc0f1599d005', total: 10, oldAbn: 0, newAbn: 1, clean: 9,
    branch: 'multi-turn blank assistant turn' },
  { id: 'f67421b0-e549-4c15-8773-3b2200cfe27b', total: 2, oldAbn: 0, newAbn: 1, clean: 1,
    branch: 'comparative answer_counts.b_blank' },
  { id: 'c433dbaa-7076-4f23-af69-08513f2a3bc1', total: 2, oldAbn: 0, newAbn: 1, clean: 1,
    branch: 'comparative per-turn scan' },
]

const report = { base: BASE, runs: [], failures: [] }
const fail = (m) => { report.failures.push(m); console.log('FAIL: ' + m) }

// Count real sample rows ONLY inside the samples table.
// Two traps this guards against:
//   - the page renders several tables (score distribution, tool usage, per-dimension), so a
//     bare `tbody tr` over-counts badly on the comparative layout (observed 15 instead of 2);
//   - the loading row and the empty-state row are also <tr>, but carry .empty-state on their <td>.
// The samples table is identified by its header carrying both 样例 and 追踪.
async function readTable(page) {
  return await page.evaluate(([tag, hCase, hTrace]) => {
    const tables = Array.from(document.querySelectorAll('table'))
    const target = tables.find(t => {
      const head = t.querySelector('thead')
      if (!head) return false
      const txt = head.textContent || ''
      return txt.includes(hCase) && txt.includes(hTrace)
    })
    if (!target) return { count: -1, withTag: -1, skipped: 0, tableFound: false, tables: tables.length }
    const all = Array.from(target.querySelectorAll('tbody tr'))
    const rows = all.filter(tr => !tr.querySelector('.empty-state'))
    return {
      count: rows.length,
      withTag: rows.filter(tr => (tr.textContent || '').includes(tag)).length,
      skipped: all.length - rows.length,
      tableFound: true,
      tables: tables.length,
    }
  }, [T.blankTag, T.thCase, T.thTrace])
}

async function readFilteredCount(page, prefix) {
  const txt = await page.evaluate((p) => {
    const els = Array.from(document.querySelectorAll('span'))
    const hit = els.find(e => (e.textContent || '').trim().startsWith(p))
    return hit ? hit.textContent.trim() : null
  }, prefix)
  if (!txt) return null
  const m = txt.match(/(\d+)/)
  return m ? Number(m[1]) : null
}

async function clickMode(page, label) {
  const btn = page.locator('button', { hasText: label }).first()
  await btn.waitFor({ state: 'visible', timeout: 15000 })
  await btn.click()
  await page.waitForTimeout(500)
}

const browser = await chromium.launch({ headless: false, slowMo: 60 })
const ctx = await browser.newContext({ storageState: AUTH, viewport: { width: 1440, height: 1200 } })
const page = await ctx.newPage()

try {
  for (const run of RUNS) {
    const tag = run.id.slice(0, 8)
    const rec = { run_id: run.id, branch: run.branch, expect: {
      total: run.total, old_abnormal: run.oldAbn, new_abnormal: run.newAbn, clean: run.clean } }

    await page.goto(`${BASE}/evaluation/runs/${run.id}`, { waitUntil: 'networkidle', timeout: 60000 })
    await page.waitForTimeout(1500)

    const hasToolbar = await page.locator(`text=${T.abnormalLabel}`).count()
    if (!hasToolbar) { fail(`${tag}: abnormal-filter toolbar not found`); report.runs.push(rec); continue }

    // 1. all
    await clickMode(page, T.all)
    const all = await readTable(page, T.blankTag)
    rec.all = all
    rec.all_chip = await readFilteredCount(page, T.filteredPrefix)
    if (all.count !== run.total) fail(`${tag}: all rendered ${all.count}, expected ${run.total}`)
    if (rec.all_chip !== null) fail(`${tag}: all should have no filter chip, got ${rec.all_chip}`)
    if (all.withTag !== run.newAbn) fail(`${tag}: all blank tags ${all.withTag}, expected ${run.newAbn}`)

    // 2. only
    await clickMode(page, T.only)
    const only = await readTable(page, T.blankTag)
    rec.only = only
    rec.only_chip = await readFilteredCount(page, T.filteredPrefix)
    if (only.count !== run.newAbn) fail(`${tag}: only rendered ${only.count}, expected ${run.newAbn}`)
    if (rec.only_chip !== run.newAbn) fail(`${tag}: only chip ${rec.only_chip}, expected ${run.newAbn}`)
    if (only.count <= run.oldAbn) {
      fail(`${tag}: only(${only.count}) did not widen past old filter(${run.oldAbn}) - #168 has no effect here`)
    }
    await page.screenshot({ path: `D:\\program\\agent_eval\\e2e\\shot-168-${tag}-only.png` })

    // 3. exclude
    await clickMode(page, T.exclude)
    const excl = await readTable(page, T.blankTag)
    rec.exclude = excl
    rec.exclude_chip = await readFilteredCount(page, T.filteredPrefix)
    if (excl.count !== run.clean) fail(`${tag}: exclude rendered ${excl.count}, expected ${run.clean}`)
    if (excl.withTag !== 0) fail(`${tag}: exclude still shows ${excl.withTag} blank-tagged rows`)
    if (rec.exclude_chip !== run.clean) fail(`${tag}: exclude chip ${rec.exclude_chip}, expected ${run.clean}`)

    // 4. partition
    if (only.count + excl.count !== run.total) {
      fail(`${tag}: only(${only.count}) + exclude(${excl.count}) != total(${run.total})`)
    }

    await page.screenshot({ path: `D:\\program\\agent_eval\\e2e\\shot-168-${tag}-exclude.png` })
    report.runs.push(rec)
  }
} catch (e) {
  fail('exception: ' + (e && e.message ? e.message : String(e)))
} finally {
  report.verdict = report.failures.length === 0 ? 'VERIFY_168_UI_OK' : 'VERIFY_168_UI_FAIL'
  writeFileSync(OUT, JSON.stringify(report, null, 1))
  console.log(JSON.stringify(report, null, 1))
  console.log(report.verdict)
  await browser.close()
}
