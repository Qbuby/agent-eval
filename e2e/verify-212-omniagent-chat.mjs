import { chromium } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const STORAGE = path.join(HERE, 'auth.json')
const EXEC = process.env.PW_CHROMIUM || 'C:\\Users\\frh\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe'

const browser = await chromium.launch({ headless: false, executablePath: EXEC })
const context = await browser.newContext({
  baseURL: BASE,
  storageState: STORAGE,
  viewport: { width: 1440, height: 900 },
})
const page = await context.newPage()

const suffix = Date.now().toString(36)
const firstPrompt = `只回复：OA_FIRST_${suffix}`
const secondPrompt = `只回复：OA_SECOND_${suffix}`
const firstAnswer = `OA_FIRST_${suffix}`
const secondAnswer = `OA_SECOND_${suffix}`
const renamed = `OmniAgent 验收 ${suffix}`

async function sendAndWait(prompt, answer) {
  const input = page.getByRole('textbox', { name: '消息输入框' })
  await input.fill(prompt)
  await input.press('Enter')
  await page.getByText(answer, { exact: true }).waitFor({ state: 'visible', timeout: 180_000 })
  await page.getByRole('button', { name: '发送', exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
}

try {
  await page.goto('/omniagent')
  await page.waitForLoadState('networkidle')
  if (page.url().includes('/login')) throw new Error('storageState 已过期，页面跳回登录')

  await page.getByRole('link', { name: '系统智能体' }).waitFor({ state: 'visible', timeout: 20_000 })
  await page.getByRole('heading', { name: 'OmniAgent 对话' }).waitFor({ state: 'visible' })

  await page.getByRole('button', { name: '新建', exact: true }).click()
  await sendAndWait(firstPrompt, firstAnswer)

  await page.getByRole('button', { name: '新建', exact: true }).click()
  await sendAndWait(secondPrompt, secondAnswer)

  // 两个会话标题由各自首条问题自动生成；切回第一个后，第二个回答不能串进来。
  await page.getByRole('button', { name: new RegExp(firstPrompt) }).click()
  await page.getByText(firstAnswer, { exact: true }).waitFor({ state: 'visible', timeout: 20_000 })
  if (await page.getByText(secondAnswer, { exact: true }).isVisible().catch(() => false)) {
    throw new Error('切换会话后仍显示第二个会话回答，消息发生串会话')
  }

  page.once('dialog', dialog => dialog.accept(renamed))
  await page.getByRole('button', { name: new RegExp(`重命名会话 ${firstPrompt}`) }).click()
  await page.getByText(renamed, { exact: true }).waitFor({ state: 'visible' })

  await page.reload()
  await page.waitForLoadState('networkidle')
  await page.getByText(renamed, { exact: true }).waitFor({ state: 'visible', timeout: 20_000 })
  await page.getByRole('button', { name: new RegExp(renamed) }).click()
  await page.getByText(firstAnswer, { exact: true }).waitFor({ state: 'visible', timeout: 20_000 })

  // 软删除第二个会话：从列表消失，但不对 OmniAgent checkpoint 做删除请求。
  await page.getByRole('button', { name: new RegExp(`删除会话 ${secondPrompt}`) }).click()
  const dialog = page.getByRole('dialog', { name: '删除会话' })
  await dialog.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByText(secondPrompt, { exact: true }).waitFor({ state: 'detached', timeout: 20_000 })

  console.log(JSON.stringify({
    ok: true,
    pageUrl: page.url(),
    firstPrompt,
    secondPrompt,
    renamed,
    firstAnswerVisible: true,
    sessionsIsolated: true,
    refreshRestored: true,
    softDeleteHidden: true,
  }, null, 2))
} finally {
  await browser.close()
}
