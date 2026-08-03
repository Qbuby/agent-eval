// #121 验收：评估器编辑抽屉里 {{Criteria}} 变量已打通固化参考要点。
// 打开「新建评估器」→ 切 LLM Judge → 断言：
//   1) 默认评估模板文本含 {{Criteria}}
//   2) 变量映射区出现 Criteria 行，默认选中 reference_criteria
//   3) 该行下拉 options 含 reference_criteria（固化参考要点）
//   4) 提示文案渲染 {{Criteria}} 取 reference_criteria 的说明
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(.:)/, '$1'))
const authPath = path.join(root, 'auth.json')
const screenshotPath = path.join(root, 'criteria-var-drawer.png')

if (!fs.existsSync(authPath)) throw new Error(`auth.json 不存在：${authPath}`)

const browser = await chromium.launch({ headless: false })
const context = await browser.newContext({
  baseURL: 'http://localhost',
  storageState: authPath,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

const checks = {}
try {
  await page.goto('/evaluators', { waitUntil: 'domcontentloaded', timeout: 30_000 })
  if (page.url().includes('/login')) throw new Error('验收登录态已失效，页面跳转到 /login')

  await page.getByRole('button', { name: '新建评估器', exact: true }).click()

  // 切到 LLM Judge 模式。ModeChip 是 <button>，label「可配置 LLM Judge」。
  const judgeToggle = page.getByRole('button', { name: '可配置 LLM Judge' }).first()
  await judgeToggle.waitFor({ state: 'visible', timeout: 10_000 })
  await judgeToggle.click()

  // 新建评估器时 evaluation_prompt 初始为空（默认模板只在 placeholder 里），
  // 映射区不渲染 Criteria 行。点「恢复默认 Prompt」把默认模板注入 value。
  const restoreBtn = page.getByRole('button', { name: '恢复默认 Prompt' }).first()
  await restoreBtn.waitFor({ state: 'visible', timeout: 10_000 })
  await restoreBtn.click()

  // 1) 默认模板含 {{Criteria}}
  const promptArea = page.locator('textarea').first()
  await promptArea.waitFor({ state: 'visible', timeout: 10_000 })
  const promptText = await promptArea.inputValue()
  checks.templateHasCriteria = promptText.includes('{{Criteria}}')

  // 2) 变量映射区 Criteria 行默认选中 reference_criteria
  const criteriaSelect = page.locator('select[aria-label="数据源 for Criteria"]')
  await criteriaSelect.waitFor({ state: 'visible', timeout: 10_000 })
  checks.criteriaMappingValue = await criteriaSelect.inputValue()

  // 3) 下拉含 reference_criteria（固化参考要点）选项
  const optionTexts = await criteriaSelect.locator('option').allTextContents()
  checks.hasReferenceCriteriaOption = optionTexts.some(t => t.includes('reference_criteria（固化参考要点）'))

  // 4) 提示文案渲染
  checks.hintRendered = await page
    .getByText('样例没填关键点时渲染为空', { exact: false })
    .isVisible()
    .catch(() => false)

  await page.screenshot({ path: screenshotPath, fullPage: true })

  const ok =
    checks.templateHasCriteria &&
    checks.criteriaMappingValue === 'reference_criteria' &&
    checks.hasReferenceCriteriaOption &&
    checks.hintRendered

  console.log(JSON.stringify({ ok, checks, screenshot: screenshotPath }, null, 2))
  if (!ok) process.exitCode = 1
} finally {
  await browser.close()
}
