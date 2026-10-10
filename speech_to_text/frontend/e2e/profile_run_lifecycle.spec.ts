import { expect, test } from '@playwright/test'

test('user can create a profile, monitor its output, and stop the workflow', async ({ page }) => {
  await page.goto('/profiles/new')

  await page.getByLabel('Name').fill('E2E Thai profile')
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByLabel('Model', { exact: true }).selectOption('small')
  await page.getByLabel('Runtime').selectOption('ctranslate2')
  await expect(page.getByLabel('Runtime')).toContainText('unavailable')
  await page.getByLabel('One keyword per line').fill('สวัสดี')
  await page.getByRole('button', { name: /Save profile/ }).click()

  await expect(page).toHaveURL(/\/profiles$/)
  await expect(page.getByRole('heading', { name: 'E2E Thai profile' })).toBeVisible()
  await expect(page.getByText('Model: small · ctranslate2')).toBeVisible()

  await page.getByRole('link', { name: 'Edit' }).click()
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('small')
  await expect(page.getByLabel('Runtime')).toHaveValue('ctranslate2')
  await page.getByLabel('Runtime').selectOption('openvino-cpu')
  await page.getByLabel('Model', { exact: true }).selectOption('tiny')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.getByText('Model: tiny · openvino-cpu')).toBeVisible()

  await page.goto('/')
  await page.getByRole('button', { name: /Start listening/ }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  await expect(page.locator('.run-kpis')).toContainText('tiny')
  await expect(page.locator('.run-kpis')).toContainText('openvino-cpu')

  await expect(page.getByText('สวัสดีครับ')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.match-list')).toContainText('สวัสดี')
  await expect(page.locator('.match-list')).toContainText('1 matches')

  await page.getByRole('link', { name: 'Back to overview' }).click()
  const workflowRow = page.locator('.run-row').filter({ hasText: 'E2E Thai profile' })
  await workflowRow.getByRole('button', { name: 'Stop workflow E2E Thai profile' }).click()
  await expect(workflowRow.locator('.status-pill')).toContainText('Stopped', { timeout: 10_000 })
})

test('stale workflow IDs explain that run history is process-local', async ({ page }) => {
  await page.goto('/runs/unknown-workflow')

  await expect(page.getByRole('status')).toContainText('This workflow is not in the current backend history.')
  await expect(page.getByRole('alert')).toHaveCount(0)
})
