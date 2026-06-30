/**
 * 多轮对话导入 · 端到端 UI 实测。
 *
 * 走完整前端链路验证 multichat 评测输出文件的导入与字段映射纠正：
 *
 *   1. 建一个临时 conversation 数据集（dataset_type=conversation）。
 *   2. 通过「导入对话」弹窗上传真实 multichat xlsx（拍平多行布局，
 *      合并单元格 conversation_id + 每行一个 turn 的 question/answer/
 *      expected_checkpoints）。
 *   3. 断言导入成功 toast（已导入 N 条，N≈50），表格出现多轮样例。
 *   4. 打开「查看」对话视图，断言字段映射正确：
 *      - question → user 气泡，answer → assistant 气泡（两者都在对话流里）
 *      - expected_checkpoints → 评判要点（criteria），可见
 *      - 期望输出（expected_output）留空（不渲染「期望输出：」标签）
 *      - 会话目标（conversation_goal）可见
 *   5. 清理：删掉临时数据集（经 API）。
 *
 * fixture：e2e/fixtures/multichat.xlsx（由真实评测输出复制而来，ASCII 名）。
 *
 * 运行：
 *   cd e2e
 *   npx playwright test tests/conversation-import.spec.ts
 */
import { expect, test } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'

const TIMESTAMP = Date.now()
const DATASET = `e2e-conv-import-${TIMESTAMP}`
const FIXTURE_FILE = path.resolve(__dirname, '..', 'fixtures', 'multichat.xlsx')

test.describe.configure({ mode: 'serial' })

test.beforeAll(() => {
  // fixture 必须存在（由真实 multichat 评测输出复制而来）。缺了直接失败，
  // 而不是静默跳过 —— 这是被测链路的核心输入。
  expect(
    fs.existsSync(FIXTURE_FILE),
    `缺少 fixture：${FIXTURE_FILE}（从真实 multichat 评测输出复制一个 xlsx 过来）`,
  ).toBeTruthy()
})

test('多轮对话导入 · xlsx 拍平布局 → 字段映射正确', async ({ page }) => {
  await page.goto('/conversations')
  await expect(page.getByRole('heading', { name: '多轮对话集', exact: true }))
    .toBeVisible({ timeout: 10_000 })

  // === 1. 建临时 conversation 数据集（列表页内联表单，创建后停在列表页）===
  await page.getByRole('button', { name: '新建数据集', exact: true }).click()
  await page.getByPlaceholder('对话数据集名称').fill(DATASET)
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText(`已创建对话数据集「${DATASET}」`))
    .toBeVisible({ timeout: 10_000 })

  // 点新建数据集卡片进详情页（/conversations/:name），样例管理都在详情页。
  await page.getByText(DATASET, { exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/conversations/${DATASET}$`), { timeout: 10_000 })

  // === 2. 导入对话文件（两步式：选文件 → 解析预览 → 确认导入）===
  await page.getByRole('button', { name: '导入对话', exact: true }).click()
  await page.locator('input[type="file"]').setInputFiles(FIXTURE_FILE)
  // 第一步：解析预览。
  await page.getByRole('button', { name: '下一步：解析预览', exact: true }).click()
  // 预览出现：解析结果统计徽标（共 N 段 / 新增 X）。首次导入应全是「新增」。
  await expect(page.getByText(/共\s*\d+\s*段/)).toBeVisible({ timeout: 30_000 })
  // 第二步：确认导入。
  await page.getByRole('button', { name: /确认导入（\d+ 段）/ }).click()

  // === 3. 导入成功 toast + 表格出现样例 ===
  // multichat xlsx 含 50 段对话（154 行经 forward-fill 聚合）。文案：
  // 「已导入 N 条对话样例」。
  const importedToast = page.getByText(/已导入\s*\d+\s*条对话样例/)
  await expect(importedToast).toBeVisible({ timeout: 30_000 })
  const toastText = (await importedToast.textContent()) || ''
  const importedCount = parseInt(toastText.match(/已导入\s*(\d+)/)?.[1] || '0', 10)
  expect(importedCount, `导入条数应 >0，实际 toast：${toastText}`).toBeGreaterThan(0)

  // 表格首行样例出现（轮数 badge）。
  const firstRow = page.locator('table tbody tr').first()
  await expect(firstRow).toBeVisible({ timeout: 10_000 })
  await expect(firstRow.getByText(/\d+\s*轮/)).toBeVisible()

  // === 4. 打开查看，校验对话视图字段映射 ===
  await firstRow.getByRole('button', { name: '查看' }).click()

  // 查看 Dialog（Dialog 组件 open=false 时 return null，故打开后页面唯一
  // 可见的 role=dialog 就是对话视图）。所有视图断言限定在此作用域内，
  // 避免误命中底层表格的「会话目标」列头等同名元素（strict mode 冲突）。
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 10_000 })

  // 会话目标可见（multichat 的 scenario → goal）。
  await expect(dialog.getByText('会话目标', { exact: true })).toBeVisible()

  // user 气泡（用户输入）+ assistant 气泡（生成答案）都在对话流里。
  // ConversationView 的 ROLE_LABEL：user→「用户」、assistant→「助手」。
  await expect(dialog.getByText('用户').first()).toBeVisible()
  await expect(dialog.getByText('助手').first()).toBeVisible()

  // 评判要点（expected_checkpoints → criteria）可见。
  await expect(dialog.getByText('评判要点：').first()).toBeVisible()

  // 期望输出（expected_output）应留空 —— 不渲染「期望输出：」标签。
  // 这是本次字段映射纠正的关键断言：answer 不再错塞进 expected_output。
  await expect(dialog.getByText('期望输出：')).toHaveCount(0)

  // 关闭查看（对话内容较长，footer 关闭按钮可能在视口外，用 Esc 最稳）
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden({ timeout: 5_000 })

  // eslint-disable-next-line no-console
  console.log(
    `[conversation-import] dataset=${DATASET} imported=${importedCount} ` +
    `字段映射断言通过：user/assistant 气泡 + 评判要点可见，期望输出留空`)
})

test('多轮对话管理 · 二次导入 upsert + 导出 + 批量删除', async ({ page }) => {
  await page.goto('/conversations')
  await expect(page.getByRole('heading', { name: '多轮对话集', exact: true }))
    .toBeVisible({ timeout: 10_000 })

  // 进入上一个 test 导入过的数据集详情页（serial 模式，数据仍在）。
  await page.getByRole('link', { name: DATASET }).click()
  await expect(page).toHaveURL(new RegExp(`/conversations/${encodeURIComponent(DATASET)}`))

  // === 1. 二次导入同一文件 → 按名 upsert，应显示「更新 N」而非全新增 ===
  await page.getByRole('button', { name: '导入对话', exact: true }).click()
  await page.locator('input[type="file"]').setInputFiles(FIXTURE_FILE)
  await page.getByRole('button', { name: '下一步：解析预览', exact: true }).click()
  // 预览里「更新」徽标应 >0（同名样例命中）。
  await expect(page.getByText(/更新\s*\d+/)).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /确认导入（\d+ 段）/ }).click()
  // 导入完成 toast 含「更新」。
  await expect(page.getByText(/已导入\s*\d+\s*条对话样例（更新\s*\d+/))
    .toBeVisible({ timeout: 30_000 })

  // === 2. 导出按钮存在且可用（选中数据集后启用）===
  // ExportMenu 是个带「导出」文案的触发按钮；点开后出现格式菜单项。
  const exportTrigger = page.getByRole('button', { name: /导出/ }).first()
  await expect(exportTrigger).toBeEnabled()

  // === 3. 批量选择 + 批量删除 ===
  // 表头全选复选框 → 选中本页所有样例 → 工具栏出现「批量删除 (N)」。
  const headerCheckbox = page.locator('table thead input[type="checkbox"]')
  await expect(headerCheckbox).toBeVisible({ timeout: 10_000 })
  await headerCheckbox.check()
  const batchDeleteBtn = page.getByRole('button', { name: /批量删除\s*\(\d+\)/ })
  await expect(batchDeleteBtn).toBeVisible()
  await batchDeleteBtn.click()
  // 确认弹窗（role=dialog）→ 点其中的「删除」确认按钮。限定在弹窗作用域，
  // 避免误命中表格每行的「删除」按钮（strict mode 冲突）。
  const confirmDialog = page.getByRole('dialog')
  await expect(confirmDialog).toBeVisible({ timeout: 5_000 })
  await confirmDialog.getByRole('button', { name: '删除', exact: true }).click()
  await expect(page.getByText(/已删除\s*\d+\s*条样例/)).toBeVisible({ timeout: 15_000 })

  // eslint-disable-next-line no-console
  console.log('[conversation-manage] 二次导入 upsert + 导出按钮 + 批量删除 断言通过')
})

// 清理：删掉临时数据集（软删，经 API）。放 afterAll 保证即使断言失败也清理。
test.afterAll(async ({ browser }) => {
  const page = await browser.newPage()
  try {
    await page.goto('/conversations')
    const token = await page.evaluate(() => {
      const raw = localStorage.getItem('agent-eval-auth')
      return raw ? JSON.parse(raw).state?.accessToken as string : null
    })
    if (token) {
      await page.request.delete(`/api/datasets/${encodeURIComponent(DATASET)}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  } finally {
    await page.close()
  }
})
