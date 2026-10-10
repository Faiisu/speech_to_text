import { expect, test } from '@playwright/test'

const report = {
  'shared-model': { topology: 'shared-model', trials: [1, 2, 4].map(workflow_count => ({
    status: 'completed', topology: 'shared-model', workflow_count, capacity_verdict: workflow_count === 1 ? 'pass' : 'fail',
    chunk_elapsed_seconds: { p50: 0.42, p95: 0.61, maximum: 0.8 }, chunk_rtf: { p50: 0.04, p95: 0.08, maximum: 0.1 },
    inference_utilization_by_model: { turbo: 0.12 }, model_startup_seconds: 1.2, peak_total_process_rss_bytes: 1024 ** 3,
    max_observed_queue_depth: workflow_count, dropped_or_failed_chunks: workflow_count === 1 ? 0 : 2, failed_inference_chunks: 0,
    chunks: [{ source_id: 'fixture-source', sequence: 0, queue_wait_seconds: 0.01, inference_seconds: 0.4, elapsed_seconds: 0.42, audio_seconds: 10, rtf: 0.04, status: 'completed' }],
  })) },
  'per-input-model': { topology: 'per-input-model', trials: [1, 2, 4].map(workflow_count => ({
    status: workflow_count === 4 ? 'unavailable' : 'completed', topology: 'per-input-model', workflow_count,
    capacity_verdict: workflow_count === 4 ? 'unavailable' : 'pass', unavailable_reason: workflow_count === 4 ? 'Memory preflight did not leave the required reserve.' : null,
    chunk_elapsed_seconds: { p50: 0.44, p95: 0.63, maximum: 0.84 }, chunk_rtf: { p50: 0.05, p95: 0.09, maximum: 0.11 },
    inference_utilization_by_model: { [`turbo-${workflow_count}`]: 0.13 }, model_startup_seconds: 1.5, peak_child_process_rss_bytes: 768 * 1024 ** 2,
    max_observed_queue_depth: null, dropped_or_failed_chunks: 0, failed_inference_chunks: 0,
    chunks: workflow_count === 4 ? [] : [{ source_id: 'fixture-source', sequence: 0, queue_wait_seconds: 0.02, inference_seconds: 0.41, elapsed_seconds: 0.44, audio_seconds: 10, rtf: 0.05, status: 'completed' }],
  })) },
}

test('capacity matrix uses backend defaults and resumes into responsive measured evidence', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1050 })
  await page.route('**/api/v1/models', route => route.fulfill({ json: {
    default_model: 'turbo', default_runtime: 'openvino-gpu', models: [{ key: 'turbo', display_name: 'Typhoon Turbo', installed: true, ready: true, runtimes: [
      { key: 'openvino-gpu', compatible: true, ready: true, precision_options: ['source', 'int8'], reason: null },
      { key: 'ctranslate2', compatible: true, ready: false, precision_options: ['int8', 'float16'], reason: 'Runtime dependencies are missing.' },
    ] }],
  } }))
  await page.route('**/api/v1/transcriptions', route => route.fulfill({ json: { transcriptions: [] } }))
  let startBody: Record<string, unknown> | undefined
  await page.route('**/api/v1/stress-tests', async route => {
    if (route.request().method() === 'POST') {
      startBody = route.request().postDataJSON()
      await route.fulfill({ status: 202, json: { stress_test_id: 'fixture-matrix', status: 'queued' } })
      return
    }
    await route.fulfill({ json: { stress_test_id: 'fixture-matrix', events: [
      { cursor: 1, type: 'trial_started', topology: 'shared-model', workflow_count: 1 },
      { cursor: 2, type: 'trial_progress', topology: 'shared-model', workflow_count: 1, completed_workflows: 0, elapsed_seconds: 2 },
    ], next_cursor: 2 } })
  })
  await page.route('**/api/v1/stress-tests/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/events')) return route.fulfill({ json: { stress_test_id: 'fixture-matrix', events: [
      { cursor: 1, type: 'trial_started', topology: 'shared-model', workflow_count: 1 },
      { cursor: 2, type: 'trial_progress', topology: 'shared-model', workflow_count: 1, completed_workflows: 0, elapsed_seconds: 2 },
    ], next_cursor: 2 } })
    return route.fulfill({ json: { stress_test_id: 'fixture-matrix', status: 'completed', model: 'turbo', runtime: 'openvino-gpu', precision: 'source', report, error: null, created_at: 1791600000 } })
  })

  await page.goto('/stress-tests')
  await expect(page.getByRole('heading', { name: 'Capacity lab' })).toBeVisible()
  await page.getByLabel('Runtime').selectOption('ctranslate2')
  await expect(page.getByText('Runtime dependencies are missing. Affected trials may be unavailable.')).toBeVisible()
  await page.getByLabel('Runtime').selectOption('openvino-gpu')
  await page.getByRole('button', { name: 'Run capacity matrix' }).click()
  await expect(page.getByText('Compare trial evidence')).toBeVisible()
  expect(startBody).toEqual({})
  await expect(page.locator('.matrix-board .trial-cell')).toHaveCount(6)
  await expect(page.getByText('capacity pass').first()).toBeVisible()
  await expect(page.getByText('Memory preflight did not leave the required reserve.', { exact: true })).toBeVisible()
  await expect(page.getByText('fixture-source').first()).toBeAttached()
  await page.screenshot({ path: '/tmp/echodesk-capacity-desktop.png', fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.topology-column').nth(0)).toBeVisible()
  await expect(page.locator('.topology-column').nth(1)).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflow).toBe(false)
  const mobileStressLink = await page.getByRole('link', { name: 'Stress test' }).boundingBox()
  expect(mobileStressLink).not.toBeNull()
  expect(mobileStressLink!.y + mobileStressLink!.height).toBeLessThanOrEqual(844)
  await page.screenshot({ path: '/tmp/echodesk-capacity-mobile.png', fullPage: true })
})

test('restores the recent stress id and recovers expired event cursors', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('echodesk.lastStressTestId', 'expired-cursor-matrix'))
  await page.route('**/api/v1/models', route => route.fulfill({ json: { default_model: 'turbo', default_runtime: 'openvino-gpu', models: [{ key: 'turbo', display_name: 'Typhoon Turbo', installed: true, ready: true, runtimes: [{ key: 'openvino-gpu', compatible: true, ready: true, precision_options: ['source'], reason: null }] }] } }))
  await page.route('**/api/v1/transcriptions', route => route.fulfill({ json: { transcriptions: [] } }))
  await page.route('**/api/v1/stress-tests/expired-cursor-matrix', route => route.fulfill({ json: { stress_test_id: 'expired-cursor-matrix', status: 'running', model: 'turbo', runtime: 'openvino-gpu', precision: 'source', report: null, error: null, created_at: 1791600000 } }))
  let eventReads = 0
  await page.route('**/api/v1/stress-tests/expired-cursor-matrix/events?after=*', route => {
    eventReads += 1
    if (eventReads === 1) return route.fulfill({ status: 410, json: { detail: { message: 'event cursor expired', oldest_cursor: 12 } } })
    expect(new URL(route.request().url()).searchParams.get('after')).toBe('11')
    return route.fulfill({ json: { stress_test_id: 'expired-cursor-matrix', events: [{ cursor: 12, type: 'trial_started', topology: 'shared-model', workflow_count: 1 }], next_cursor: 12 } })
  })
  await page.goto('/stress-tests')
  await expect(page.getByText(/Some earlier progress events expired/)).toBeVisible()
  await expect(page.locator('.cell-preparing')).toBeVisible()
  await expect(page.getByText('expired-cursor-matrix').first()).toBeVisible()
})

test('sends only selected precision overrides and restores after a transient status error', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('echodesk.lastStressTestId', 'retry-matrix'))
  await page.route('**/api/v1/models', route => route.fulfill({ json: { default_model: 'turbo', default_runtime: 'openvino-gpu', models: [{ key: 'turbo', display_name: 'Typhoon Turbo', installed: true, ready: true, runtimes: [{ key: 'openvino-gpu', compatible: true, ready: true, precision_options: ['source', 'int8'], reason: null }] }] } }))
  await page.route('**/api/v1/transcriptions', route => route.fulfill({ json: { transcriptions: [] } }))
  let statusReads = 0
  let posted: Record<string, unknown> | undefined
  await page.route('**/api/v1/stress-tests/retry-matrix', route => {
    statusReads += 1
    if (statusReads === 1) return route.fulfill({ status: 503, json: { detail: 'Temporary status read failure.' } })
    return route.fulfill({ json: { stress_test_id: 'retry-matrix', status: 'completed', model: 'turbo', runtime: 'openvino-gpu', precision: 'source', report: null, error: null, created_at: 1791600000 } })
  })
  await page.route('**/api/v1/stress-tests/retry-matrix/events?after=*', route => route.fulfill({ json: { stress_test_id: 'retry-matrix', events: [], next_cursor: 0 } }))
  await page.route('**/api/v1/stress-tests', route => {
    posted = route.request().postDataJSON()
    return route.fulfill({ status: 202, json: { stress_test_id: 'new-matrix', status: 'queued' } })
  })
  await page.route('**/api/v1/stress-tests/new-matrix/**', route => route.fulfill({ json: { stress_test_id: 'new-matrix', events: [], next_cursor: 0 } }))
  await page.route('**/api/v1/stress-tests/new-matrix', route => route.fulfill({ json: { stress_test_id: 'new-matrix', status: 'completed', model: 'turbo', runtime: 'openvino-gpu', precision: 'int8', report: null, error: null, created_at: 1791600000 } }))

  await page.goto('/stress-tests')
  await expect(page.getByRole('alert')).toContainText('Temporary status read failure.')
  await expect(page.getByText('Run retry-matrix')).toBeVisible()
  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect(page.getByText('completed', { exact: true })).toBeVisible()
  await page.getByLabel('Precision').selectOption('int8')
  await page.getByRole('button', { name: 'Run capacity matrix' }).click()
  await expect(page.getByText('Run new-matrix')).toBeVisible()
  expect(posted).toEqual({ precision: 'int8' })
})

test('a missing restored run clears local history and reports bounded-history expiry', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('echodesk.lastStressTestId', 'gone-matrix'))
  await page.route('**/api/v1/models', route => route.fulfill({ json: { default_model: 'turbo', default_runtime: 'openvino-gpu', models: [{ key: 'turbo', display_name: 'Typhoon Turbo', installed: true, ready: true, runtimes: [{ key: 'openvino-gpu', compatible: true, ready: true, precision_options: ['source'], reason: null }] }] } }))
  await page.route('**/api/v1/transcriptions', route => route.fulfill({ json: { transcriptions: [] } }))
  await page.route('**/api/v1/stress-tests/gone-matrix', route => route.fulfill({ status: 404, json: { detail: 'stress test not found' } }))
  await page.goto('/stress-tests')
  await expect(page.getByText('This run is no longer available.')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('echodesk.lastStressTestId'))).toBeNull()
  await expect(page.getByRole('button', { name: 'Run capacity matrix' })).toBeEnabled()
})

test('refreshes microphone state after a concurrent start conflict', async ({ page }) => {
  let transcriptionReads = 0
  await page.route('**/api/v1/models', route => route.fulfill({ json: { default_model: 'turbo', default_runtime: 'openvino-gpu', models: [{ key: 'turbo', display_name: 'Typhoon Turbo', installed: true, ready: true, runtimes: [{ key: 'openvino-gpu', compatible: true, ready: true, precision_options: ['source'], reason: null }] }] } }))
  await page.route('**/api/v1/transcriptions', route => {
    transcriptionReads += 1
    return route.fulfill({ json: { transcriptions: transcriptionReads <= 2 ? [] : [{ workflow_id: 'active-mic-run', kind: 'microphone', status: 'recording', keywords: [], profile_id: null, profile_name: 'Studio microphone', device: 'Studio Mic', execution_mode: 'shared', silence_threshold: null, model: 'turbo', runtime: 'openvino-gpu', transcript: null, matches: null, latest_rtf: null, error: null, created_at: '2026-10-10T00:00:00Z' }] } })
  })
  await page.route('**/api/v1/stress-tests', route => route.fulfill({ status: 409, json: { detail: 'a microphone workflow is active' } }))
  await page.goto('/stress-tests')
  await page.getByRole('button', { name: 'Run capacity matrix' }).click()
  await expect(page.getByRole('alert')).toContainText('a microphone workflow is active')
  await expect(page.getByText('Microphone workflow is active', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open workflow' })).toHaveAttribute('href', '/runs/active-mic-run')
  await expect(page.getByRole('button', { name: 'Run capacity matrix' })).toBeDisabled()
})
