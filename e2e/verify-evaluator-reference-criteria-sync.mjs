import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(.:)/, '$1'))
const baseURL = process.env.BASE_URL || 'http://localhost'
const userFile = path.join(root, 'test-user.json')
const screenshotPath = path.join(root, 'evaluator-reference-criteria-sync.png')
const executablePath = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'

const expectedNames = [
  '多轮-任务完成度',
  '多轮-任务完成度对比',
  '多轮-回答正确性',
  '多轮-回答正确性对比',
  '多轮-安全与拒答恰当性',
  '多轮-安全与拒答恰当性对比',
  '多轮-对话连贯性',
  '多轮-对话连贯性对比',
  '多轮-工具调用正确性',
  '多轮-工具调用正确性对比',
  '多轮-指令遵循',
  '多轮-指令遵循对比',
  '幻觉率/agent',
  '幻觉率/llm-judge',
  '幻觉率对比/llm-judge',
  '正确性/agent',
  '正确性/llm-judge',
  '正确性对比/llm-judge',
  '简洁度/agent',
  '简洁度/llm-judge',
  '简洁度对比/llm-judge',
]

const representatives = [
  { name: '多轮-任务完成度', variable: 'Checklist' },
  { name: '多轮-任务完成度对比', variable: 'Criteria' },
  { name: '正确性/llm-judge', variable: 'Criteria' },
]

if (!fs.existsSync(userFile)) throw new Error(`测试用户文件不存在：${userFile}`)
const user = JSON.parse(fs.readFileSync(userFile, 'utf8'))
const browser = await chromium.launch({ headless: false, executablePath })
const context = await browser.newContext({ baseURL, viewport: { width: 1500, height: 950 } })
const page = await context.newPage()

try {
  await page.goto('/login', { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await page.getByPlaceholder('输入用户名').fill(user.username)
  await page.getByPlaceholder('输入密码').fill(user.password)
  await page.getByRole('button', { name: /继续|登录/ }).click()
  await page.waitForURL(url => !url.pathname.includes('/login'), { timeout: 20_000 })

  await page.goto('/evaluators', { waitUntil: 'domcontentloaded', timeout: 30_000 })
  await page.getByText('加载中…', { exact: true }).waitFor({ state: 'hidden', timeout: 20_000 })
  await page.locator('table tbody td:first-child').first().waitFor({ state: 'visible', timeout: 20_000 })

  const rows = page.locator('table tbody tr')
  const visibleNames = (await rows.locator('td:first-child').allTextContents()).map(name => name.trim())
  const missing = expectedNames.filter(name => !visibleNames.includes(name))
  if (missing.length) throw new Error(`UI 缺少迁移评估器：${missing.join('、')}`)

  const checks = []
  for (const representative of representatives) {
    await page.goto('/evaluators', { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await page.getByText('加载中…', { exact: true }).waitFor({ state: 'hidden', timeout: 20_000 })
    await page.locator('table tbody td:first-child').first().waitFor({ state: 'visible', timeout: 20_000 })

    const row = page.locator('table tbody tr').filter({
      has: page.getByText(representative.name, { exact: true }),
    }).first()
    await row.waitFor({ state: 'visible', timeout: 10_000 })
    const actualName = (await row.locator('td:first-child').textContent())?.trim()
    if (actualName !== representative.name) {
      throw new Error(`评估器行匹配错误：期望 ${representative.name}，实际 ${actualName}`)
    }

    await row.getByRole('button', { name: '编辑', exact: true }).click()
    await page.getByText('编辑评估器', { exact: true }).waitFor({ state: 'visible', timeout: 10_000 })

    const source = page.locator(`select[aria-label="数据源 for ${representative.variable}"]`)
    await source.waitFor({ state: 'visible', timeout: 10_000 })
    const mapping = await source.inputValue()
    if (mapping !== 'reference_criteria') {
      throw new Error(`${representative.name} 的 ${representative.variable} 映射为 ${mapping}`)
    }

    const promptValues = await page.locator('textarea').evaluateAll(items =>
      items.map(item => item.value),
    )
    const placeholder = `{{${representative.variable}}}`
    if (!promptValues.some(value => value.includes(placeholder))) {
      throw new Error(`${representative.name} 的 Prompt 缺少 ${placeholder}`)
    }

    checks.push({
      name: representative.name,
      variable: representative.variable,
      mapping,
      promptHasVariable: true,
    })
  }

  await page.screenshot({ path: screenshotPath, fullPage: true })
  console.log(JSON.stringify({
    ok: true,
    visibleTargetCount: expectedNames.length,
    checks,
    screenshot: screenshotPath,
  }, null, 2))
} finally {
  await browser.close()
}
