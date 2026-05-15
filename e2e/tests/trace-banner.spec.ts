import { expect, test } from '@playwright/test'

// Quick probe: log in, take an existing run, trigger backfill_trace, verify
// the new "查询失败" banner appears with the forbidden message.
test('trace lookup banner shows specific 403 cause', async ({ page, request }) => {
  // We're logged in via storageState, but backfill_trace also needs an HTTP-
  // level auth header — go through the UI to keep cookies/headers consistent.
  await page.goto('/evaluation')
  await expect(page.getByRole('heading', { name: '评估', exact: true })).toBeVisible()
  // Pick the first completed run
  const firstRow = page.locator('tr', { hasText: /completed/i }).first()
  await firstRow.click()
  await page.waitForURL(/\/evaluation\/runs\/[0-9a-f-]+/, { timeout: 15_000 })

  await page.getByPlaceholder(/例如\s*ruyi-agent/).fill('i-have-no-permission-here')

  const resp = page.waitForResponse(r =>
    r.url().includes('/backfill_trace') && r.status() === 200,
    { timeout: 30_000 },
  )
  await page.getByRole('button', { name: /查询轨迹/ }).click()
  const r = await resp
  const body = await r.json()

  // API contract
  expect(body.error_kind).toBe('forbidden')
  expect(body.matched).toBe(0)
  expect(body.errors).toBeGreaterThan(0)

  // UI contract: the "查询失败 ·" red banner must appear with the 403 wording
  await expect(page.getByText(/查询失败\s*·/)).toBeVisible()
  await expect(page.getByText(/没有读权限.*403/)).toBeVisible()
})
