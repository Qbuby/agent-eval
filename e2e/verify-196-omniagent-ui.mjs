import { chromium } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const STORAGE = path.join(HERE, 'auth.json')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'
const COMPOSE_URL = 'http://omniagent:8090/api/agent/langgraph'
const COMPOSE_LABEL = 'OmniAgent（Docker Compose）'
const SIDECAR_URL = 'http://127.0.0.1:8090/api/agent/langgraph'
const SIDECAR_LABEL = 'OmniAgent（同 Pod sidecar）'

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
  composeCount: null,
  sidecarCount: null,
  composeOptionVisible: false,
  sidecarOptionVisible: false,
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
  out.composeCount = (config.body.options || []).filter(
    o => o.value === COMPOSE_URL,
  ).length
  out.sidecarCount = (config.body.options || []).filter(
    o => o.value === SIDECAR_URL && o.label === SIDECAR_LABEL,
  ).length

  if (config.status !== 200) throw new Error(`配置 API 返回 ${config.status}`)
  if (out.composeCount !== 1) throw new Error(`Compose 预设数量错误: ${out.composeCount}`)
  if (out.sidecarCount !== 1) throw new Error(`sidecar 预设数量错误: ${out.sidecarCount}`)
  if (out.defaultIndex !== 0) throw new Error(`默认索引被改动: ${out.defaultIndex}`)
  if ([COMPOSE_URL, SIDECAR_URL].includes(out.defaultValue)) {
    throw new Error('OmniAgent 被意外设为默认值')
  }

  const urlField = page.locator('label').filter({ hasText: '智能体 URL' }).first()
  await urlField.waitFor({ state: 'visible', timeout: 20_000 })
  const input = urlField.locator('input')
  const picker = urlField.getByRole('button', { name: '选择预设值' })
  await picker.click()

  const composeOption = page.getByRole('option').filter({ hasText: COMPOSE_URL })
  const sidecarOption = page.getByRole('option', { name: new RegExp(SIDECAR_LABEL) })
  await composeOption.waitFor({ state: 'visible', timeout: 10_000 })
  await sidecarOption.waitFor({ state: 'visible', timeout: 10_000 })
  out.composeOptionVisible = true
  out.sidecarOptionVisible = true
  await composeOption.click()
  out.selectedValue = await input.inputValue()

  if (out.selectedValue !== COMPOSE_URL) {
    throw new Error(`URL 回填错误: ${out.selectedValue}`)
  }

  console.log(JSON.stringify({ ok: true, ...out }, null, 2))
} finally {
  await browser.close()
}
