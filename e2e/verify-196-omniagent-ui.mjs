import { chromium } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const STORAGE = path.join(HERE, 'auth.json')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'
const OMNI_URL = 'http://omniagent:8090/api/agent/langgraph'
const OMNI_LABEL = 'OmniAgent（系统智能体）'

const browser = await chromium.launch({ headless: false, executablePath: EXEC })
const context = await browser.newContext({
  baseURL: BASE,
  storageState: STORAGE,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

const out = {
  pageUrl: null,
  configStatus: null,
  defaultIndex: null,
  defaultValue: null,
  omniCount: null,
  optionVisible: false,
  selectedValue: null,
}

try {
  await page.goto('/evaluation')
  await page.waitForLoadState('networkidle')
  if (page.url().includes('/login')) throw new Error('storageState 已过期，页面跳回登录')
  out.pageUrl = page.url()

  await page.getByRole('button', { name: '新建评估', exact: true }).first().click()
  await page.getByText('2. 配置智能体', { exact: true }).waitFor({ state: 'visible', timeout: 20_000 })

  const config = await page.evaluate(async () => {
    const raw = localStorage.getItem('agent-eval-auth')
    const parsed = raw ? JSON.parse(raw) : null
    const token = parsed?.state?.accessToken || localStorage.getItem('access_token')
    const response = await fetch('/api/config/target_agent.endpoint_url', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    return { status: response.status, body: await response.json() }
  })
  out.configStatus = config.status
  out.defaultIndex = config.body.default_index
  out.defaultValue = config.body.value
  out.omniCount = (config.body.options || []).filter(o => o.value === OMNI_URL && o.label === OMNI_LABEL).length

  if (config.status !== 200) throw new Error(`配置 API 返回 ${config.status}`)
  if (out.omniCount !== 1) throw new Error(`OmniAgent 预设数量错误: ${out.omniCount}`)
  if (out.defaultIndex !== 0) throw new Error(`默认索引被改动: ${out.defaultIndex}`)
  if (out.defaultValue === OMNI_URL) throw new Error('OmniAgent 被意外设为默认值')

  const urlField = page.locator('label').filter({ hasText: '智能体 URL' }).first()
  await urlField.waitFor({ state: 'visible', timeout: 20_000 })
  const input = urlField.locator('input')
  const picker = urlField.getByRole('button', { name: '选择预设值' })
  await picker.click()

  const option = page.getByRole('option', { name: new RegExp(OMNI_LABEL) })
  await option.waitFor({ state: 'visible', timeout: 10_000 })
  out.optionVisible = true
  await option.click()
  out.selectedValue = await input.inputValue()

  if (out.selectedValue !== OMNI_URL) {
    throw new Error(`URL 回填错误: ${out.selectedValue}`)
  }

  console.log(JSON.stringify({ ok: true, ...out }, null, 2))
} finally {
  await browser.close()
}
