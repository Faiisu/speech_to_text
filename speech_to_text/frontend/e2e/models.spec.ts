import { expect, test } from '@playwright/test'

const runtimes = [
  { key: 'ctranslate2', available: true, precision_options: ['int8', 'float32'], reason: null },
  { key: 'openvino-cpu', available: false, precision_options: [], reason: 'OpenVINO is not installed on this host.' },
]

const modelCatalog = (ready = false) => ({
  default_model: 'turbo', default_runtime: 'ctranslate2',
  models: [{ key: 'turbo', display_name: 'Whisper Turbo', repository: null, revision: null, installed: true, ready: true, runtimes: [{ key: 'ctranslate2', compatible: true, ready: true, precision_options: ['int8'], reason: null, default_precision: 'int8', has_generic_artifact: true, installed_precisions: [] }] },
    { key: 'whisper-tiny', display_name: 'Whisper Tiny', repository: 'openai/whisper-tiny', revision: 'pinned-tiny-sha', installed: true, ready: true, runtimes: [{ key: 'ctranslate2', compatible: true, ready: true, precision_options: ['int8', 'float32'], reason: null, default_precision: 'int8', has_generic_artifact: false, installed_precisions: ready ? ['int8'] : ['float32'] }] },
    { key: 'not-ready', display_name: 'Installed but unavailable', installed: true, ready: false, runtimes: [] },
    { key: 'not-prepared', display_name: 'Not prepared', installed: false, ready: false, runtimes: [] },
    { key: 'source-only', display_name: 'Source-only Whisper', repository: 'org/source-only', revision: 'saved-source-sha', installed: false, ready: false, runtimes: [] }],
})

const inspection = (repository: string) => repository.includes('not-whisper')
  ? { repository, revision: 'deadbeef', compatible: false, reasons: ['Only Transformers Whisper checkpoints are supported.'], model_type: 'wav2vec2', runtimes }
  : { repository, revision: '1a2b3c4d', compatible: true, reasons: [], model_type: 'whisper', runtimes }

const emptyJobs = { jobs: [] }
const deletionPlan = (model_key: string, overrides: Record<string, unknown> = {}) => ({
  model_key,
  files: [{ path: `sources/${model_key}`, kind: 'source', bytes: 1024 }, { path: `ctranslate2-${model_key}-int8`, kind: 'runtime', bytes: 2048 }],
  total_bytes: 3072,
  registered: true,
  deletable: true,
  reason: null,
  ...overrides,
})

test('inspects compatible and incompatible repositories and blocks unsupported conversion', async ({ page }) => {
  let inspected: Record<string, unknown> | null = null
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/inspect', async route => {
    inspected = route.request().postDataJSON()
    await route.fulfill({ json: inspection(String(inspected.repository)) })
  })

  await page.goto('/models')
  await expect(page.getByRole('heading', { name: 'Models', exact: true })).toBeVisible()
  await expect(page.getByText('NOT READY', { exact: true })).toBeVisible()
  await expect(page.getByText('NOT PREPARED', { exact: true }).first()).toBeVisible()
  await page.locator('#model-token').fill('hf_secret_for_this_request')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await expect(page.getByText('Whisper source is compatible')).toBeVisible()
  expect(inspected).toMatchObject({ repository: 'openai/whisper-tiny', revision: 'main', token: 'hf_secret_for_this_request' })
  await expect(page.locator('#model-token')).toHaveValue('')
  expect(await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage }))).not.toContain('hf_secret_for_this_request')
  await expect(page.getByText('OpenVINO is not installed on this host.')).toBeVisible()
  await expect(page.getByText('Workflow default: int8; available: float32')).toBeVisible()
  await expect(page.getByText('Installed precisions: not recorded (legacy artifact)')).toBeVisible()
  await page.locator('#model-precision').selectOption('float32')
  await expect(page.locator('.models-precision-note')).toContainText('This job prepares float32')
  await page.locator('#model-key').fill('new-whisper')
  await expect(page.locator('.models-precision-note')).toContainText('available installed precisions: none')
  await page.locator('#model-runtime').selectOption('openvino-cpu')
  await expect(page.getByRole('button', { name: 'Download and convert' })).toBeDisabled()

  await page.locator('#model-repository').fill('example/not-whisper')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await expect(page.getByText('This source cannot be prepared')).toBeVisible()
  await expect(page.getByText('wav2vec2', { exact: true })).toBeVisible()
  await expect(page.getByText('Only Transformers Whisper checkpoints are supported.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download source' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Download and convert' })).toBeDisabled()
})

test('restores active jobs after reload, completes conversion, refreshes catalog, and clears the token', async ({ page }) => {
  let job: Record<string, unknown> | null = null
  let finishJob = false
  let catalogReady = false
  let submitted: Record<string, unknown> | null = null
  const queued = () => ({ job_id: 'job-convert-1', status: 'queued', phase: 'Waiting for preparation worker', repository: 'openai/whisper-tiny', model_key: 'whisper-tiny', revision: '1a2b3c4d', operation: 'convert', runtime: 'ctranslate2', precision: 'int8', created_at: '2026-10-17T00:00:00Z', updated_at: '2026-10-17T00:00:00Z', error: null, result: null })
  const completed = () => ({ ...queued(), status: 'completed', phase: 'Inference verification passed', result: { model_key: 'whisper-tiny', repository: 'openai/whisper-tiny', revision: '1a2b3c4d', downloaded: true, converted: true, verified: true, runtime: 'ctranslate2', precision: 'int8', reason: null } })

  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog(catalogReady) }))
  await page.route('**/api/v1/models/jobs', async route => {
    if (route.request().method() === 'POST') {
      submitted = route.request().postDataJSON()
      job = queued()
      await route.fulfill({ status: 202, json: job })
      return
    }
    if (job && finishJob) {
      job = completed()
      catalogReady = true
    }
    await route.fulfill({ json: { jobs: job ? [job] : [] } })
  })
  await page.route('**/api/v1/models/inspect', route => route.fulfill({ json: inspection('openai/whisper-tiny') }))

  await page.goto('/models')
  await page.locator('#model-token').fill('hf_request_only')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await expect(page.getByText('Whisper source is compatible')).toBeVisible()
  await expect(page.locator('#model-token')).toHaveValue('')
  await page.locator('#model-token').fill('hf_request_only')
  await page.getByRole('button', { name: 'Download and convert' }).click()
  await expect(page.getByText('queued', { exact: true })).toBeVisible()
  expect(submitted).toMatchObject({ token: 'hf_request_only', operation: 'convert', runtime: 'ctranslate2', precision: 'int8' })
  await expect(page.getByRole('button', { name: 'Download source' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Download and convert' })).toBeDisabled()
  await expect(page.locator('#model-token')).toHaveValue('')
  expect(await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage }))).not.toContain('hf_request_only')

  await page.reload()
  await expect(page.getByText('queued', { exact: true })).toBeVisible()
  finishJob = true
  await expect(page.getByText('Inference verified')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.models-readiness.ready').last()).toContainText('READY')
  await expect(page.getByText('Inference not verified')).toHaveCount(0)
})

test('downloads a source without runtime conversion or claiming inference readiness', async ({ page }) => {
  let job: Record<string, unknown> | null = null
  let submitted: Record<string, unknown> | null = null
  const finished = () => ({ job_id: 'job-download-1', status: 'completed', phase: 'Source download complete', repository: 'openai/whisper-tiny', model_key: 'whisper-tiny', revision: '1a2b3c4d', operation: 'download', runtime: 'ctranslate2', precision: 'int8', created_at: '2026-10-17T00:00:00Z', updated_at: '2026-10-17T00:00:02Z', error: null, result: { model_key: 'whisper-tiny', repository: 'openai/whisper-tiny', revision: '1a2b3c4d', downloaded: true, converted: false, verified: false, runtime: 'ctranslate2', precision: 'int8', reason: null } })
  const catalog = modelCatalog().models.map(model => model.key === 'whisper-tiny'
    ? { ...model, installed: false, ready: false, runtimes: model.runtimes.map(runtime => ({ ...runtime, ready: false, installed_precisions: [] })) }
    : model)

  await page.route('**/api/v1/models', route => route.fulfill({ json: { default_model: 'turbo', default_runtime: 'ctranslate2', default_precision: 'int8', models: catalog } }))
  await page.route('**/api/v1/models/jobs', async route => {
    if (route.request().method() === 'POST') {
      submitted = route.request().postDataJSON()
      job = finished()
      await route.fulfill({ status: 202, json: { ...job, status: 'queued', phase: 'Queued' } })
      return
    }
    await route.fulfill({ json: { jobs: job ? [job] : [] } })
  })
  await page.route('**/api/v1/models/inspect', route => route.fulfill({ json: inspection('openai/whisper-tiny') }))

  await page.goto('/models')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await expect(page.getByText('Whisper source is compatible')).toBeVisible()
  await page.getByRole('button', { name: 'Download source' }).click()
  await expect(page.getByText('Source saved')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Inference not verified')).toBeVisible()
  expect(submitted).toMatchObject({ repository: 'openai/whisper-tiny', model_key: 'whisper-tiny', revision: 'main', operation: 'download' })
  expect(submitted).not.toHaveProperty('runtime')
  expect(submitted).not.toHaveProperty('precision')
  await expect(page.locator('.models-catalog-item').filter({ hasText: 'Whisper Tiny' }).locator('.models-readiness')).toHaveText('NOT PREPARED')
})

test('selects a catalog model and prepares another precision from its pinned source', async ({ page }) => {
  let inspected: Record<string, unknown> | null = null
  let submitted: Record<string, unknown> | null = null
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', async route => {
    if (route.request().method() === 'POST') {
      submitted = route.request().postDataJSON()
      await route.fulfill({ status: 202, json: { job_id: 'selected-model-job', status: 'queued', phase: 'Queued', repository: 'openai/whisper-tiny', model_key: 'whisper-tiny', revision: 'pinned-tiny-sha', operation: 'convert', runtime: 'ctranslate2', precision: 'float32', created_at: '2026-10-17T00:00:00Z', updated_at: '2026-10-17T00:00:00Z', error: null, result: null } })
      return
    }
    await route.fulfill({ json: emptyJobs })
  })
  await page.route('**/api/v1/models/inspect', async route => {
    inspected = route.request().postDataJSON()
    await route.fulfill({ json: inspection('openai/whisper-tiny') })
  })

  await page.goto('/models')
  await expect(page.locator('.models-catalog-action').filter({ has: page.getByRole('button', { name: 'Prepare another runtime' }) }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Prepare another runtime' }).first().click()
  await expect(page.locator('#model-key')).toHaveValue('whisper-tiny')
  await expect(page.locator('#model-repository')).toHaveValue('openai/whisper-tiny')
  await expect(page.locator('#model-revision')).toHaveValue('pinned-tiny-sha')
  await expect(page.getByText('Whisper source is compatible')).toBeVisible()
  expect(inspected).toMatchObject({ repository: 'openai/whisper-tiny', revision: 'pinned-tiny-sha' })
  await expect(page.getByRole('button', { name: 'Download source' })).toHaveCount(0)
  await page.locator('#model-precision').selectOption('float32')
  await page.getByRole('button', { name: 'Prepare runtime' }).click()
  expect(submitted).toMatchObject({ repository: 'openai/whisper-tiny', model_key: 'whisper-tiny', revision: 'pinned-tiny-sha', operation: 'convert', runtime: 'ctranslate2', precision: 'float32' })
  await expect(page.getByText('queued', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Preparation unavailable · source unknown' }).first()).toBeDisabled()
})

test('a slow inspection for an earlier catalog selection cannot replace a newer model', async ({ page }) => {
  let releaseEarlier: (() => void) | undefined
  let finishEarlier: (() => void) | undefined
  const earlierFinished = new Promise<void>(resolve => { finishEarlier = resolve })
  const catalog = modelCatalog().models
  catalog.push({ key: 'whisper-small', display_name: 'Whisper Small', repository: 'openai/whisper-small', revision: 'pinned-small-sha', installed: true, ready: true, runtimes: [{ key: 'ctranslate2', compatible: true, ready: true, precision_options: ['int8', 'float32'], reason: null, default_precision: 'int8', installed_precisions: ['int8'] }] })
  await page.route('**/api/v1/models', route => route.fulfill({ json: { default_model: 'turbo', default_runtime: 'ctranslate2', default_precision: 'int8', models: catalog } }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/inspect', async route => {
    const body = route.request().postDataJSON() as { repository: string }
    if (body.repository === 'openai/whisper-tiny') {
      await new Promise<void>(resolve => { releaseEarlier = resolve })
      await route.fulfill({ json: inspection(body.repository) })
      finishEarlier?.()
      return
    }
    await route.fulfill({ json: inspection(body.repository) })
  })

  await page.goto('/models')
  await page.locator('.models-catalog-item').filter({ hasText: 'Whisper Tiny' }).getByRole('button', { name: 'Prepare another runtime' }).click()
  await page.locator('.models-catalog-item').filter({ hasText: 'Whisper Small' }).getByRole('button', { name: 'Prepare another runtime' }).click()
  await expect(page.locator('#model-repository')).toHaveValue('openai/whisper-small')
  await expect(page.locator('.models-source-meta')).toContainText('openai/whisper-small')
  releaseEarlier?.()
  await earlierFinished
  await expect(page.locator('.models-source-meta')).toContainText('openai/whisper-small')
  await expect(page.locator('#model-revision')).toHaveValue('pinned-small-sha')
})

test('selects a downloaded source-only model with its saved revision', async ({ page }) => {
  let inspected: Record<string, unknown> | null = null
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/inspect', async route => {
    inspected = route.request().postDataJSON()
    await route.fulfill({ json: inspection('org/source-only') })
  })

  await page.goto('/models')
  const sourceOnly = page.locator('.models-catalog-item').filter({ hasText: 'Source-only Whisper' })
  await sourceOnly.getByRole('button', { name: 'Convert model' }).click()
  await expect(page.locator('#model-key')).toHaveValue('source-only')
  await expect(page.locator('#model-repository')).toHaveValue('org/source-only')
  await expect(page.locator('#model-revision')).toHaveValue('saved-source-sha')
  await expect(page.getByText('Whisper source is compatible')).toBeVisible()
  expect(inspected).toMatchObject({ repository: 'org/source-only', revision: 'saved-source-sha' })
  await expect(page.getByRole('button', { name: 'Prepare runtime' })).toBeEnabled()
})

test('previews actual local assets and Cancel sends no delete request', async ({ page }) => {
  let deleteRequests = 0
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/not-prepared/deletion', route => route.fulfill({ json: deletionPlan('not-prepared') }))
  await page.route('**/api/v1/models/not-prepared', route => {
    if (route.request().method() === 'DELETE') deleteRequests++
    return route.fulfill({ status: 200, json: { ...deletionPlan('not-prepared'), deleted: true } })
  })

  await page.goto('/models')
  const row = page.locator('.models-catalog-item').nth(3)
  await row.getByRole('button', { name: 'Delete local model' }).click()
  const confirmation = row.getByRole('region', { name: 'Delete confirmation for not-prepared' })
  await expect(confirmation.getByText('sources/not-prepared', { exact: true })).toBeVisible()
  await expect(confirmation.getByText('ctranslate2-not-prepared-int8', { exact: true })).toBeVisible()
  await expect(confirmation).toContainText('3.0 KB total')
  await confirmation.getByRole('button', { name: 'Cancel' }).click()
  await expect(confirmation).toHaveCount(0)
  expect(deleteRequests).toBe(0)
})

test('confirmed deletion refreshes the catalog and clears a selected preparation model', async ({ page }) => {
  let deleted = false
  let deleteRequests = 0
  await page.route('**/api/v1/models', route => {
    const catalog = modelCatalog()
    route.fulfill({ json: deleted ? { ...catalog, models: catalog.models.filter(model => model.key !== 'source-only') } : catalog })
  })
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/inspect', route => route.fulfill({ json: inspection('org/source-only') }))
  await page.route('**/api/v1/models/source-only/deletion', route => route.fulfill({ json: deletionPlan('source-only', { files: [{ path: 'sources/source-only', kind: 'source', bytes: 1024 }], total_bytes: 1024 }) }))
  await page.route('**/api/v1/models/source-only', route => {
    if (route.request().method() === 'DELETE') {
      deleteRequests++
      deleted = true
      return route.fulfill({ status: 200, json: { ...deletionPlan('source-only'), deleted: true } })
    }
    return route.continue()
  })

  await page.goto('/models')
  const row = page.locator('.models-catalog-item').filter({ hasText: 'Source-only Whisper' })
  await row.getByRole('button', { name: 'Convert model' }).click()
  await expect(page.locator('#model-repository')).toHaveValue('org/source-only')
  await row.getByRole('button', { name: 'Delete local model' }).click()
  const confirmation = row.getByRole('region', { name: 'Delete confirmation for source-only' })
  await confirmation.getByRole('button', { name: 'Delete local model' }).click()
  await expect(row).toHaveCount(0)
  expect(deleteRequests).toBe(1)
  await expect(page.locator('#model-key')).toHaveValue('')
  await expect(page.locator('#model-repository')).toHaveValue('')
  await expect(page.locator('#model-revision')).toHaveValue('main')
  await expect(page.locator('.models-catalog-item').filter({ hasText: 'Whisper Turbo' })).toBeVisible()
})

test('blocked preview explains the guard and never sends DELETE', async ({ page }) => {
  let deleteRequests = 0
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/not-prepared/deletion', route => route.fulfill({ json: deletionPlan('not-prepared', { deletable: false, reason: 'A saved profile uses this model.' }) }))
  await page.route('**/api/v1/models/not-prepared', route => {
    if (route.request().method() === 'DELETE') deleteRequests++
    return route.fulfill({ status: 409, json: { detail: 'A saved profile uses this model.' } })
  })

  await page.goto('/models')
  const row = page.locator('.models-catalog-item').nth(3)
  await row.getByRole('button', { name: 'Delete local model' }).click()
  const confirmation = row.getByRole('region', { name: 'Delete confirmation for not-prepared' })
  await expect(confirmation.getByText('A saved profile uses this model.')).toBeVisible()
  await expect(confirmation.getByRole('button', { name: 'Delete local model' })).toBeDisabled()
  expect(deleteRequests).toBe(0)
})

test('server deletion failure leaves the model visible and confirmation retryable', async ({ page }) => {
  let deleteRequests = 0
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/not-prepared/deletion', route => route.fulfill({ json: deletionPlan('not-prepared') }))
  await page.route('**/api/v1/models/not-prepared', route => {
    if (route.request().method() === 'DELETE') deleteRequests++
    return route.fulfill({ status: 503, json: { detail: 'Local storage could not be updated.' } })
  })

  await page.goto('/models')
  const row = page.locator('.models-catalog-item').nth(3)
  await row.getByRole('button', { name: 'Delete local model' }).click()
  const confirmation = row.getByRole('region', { name: 'Delete confirmation for not-prepared' })
  await confirmation.getByRole('button', { name: 'Delete local model' }).click()
  await expect(confirmation.getByRole('alert')).toContainText('Local storage could not be updated.')
  await expect(row).toBeVisible()
  await expect(confirmation.getByRole('button', { name: 'Retry delete local model' })).toBeEnabled()
  expect(deleteRequests).toBe(1)
  await confirmation.getByRole('button', { name: 'Cancel' }).click()
  await expect(confirmation).toHaveCount(0)
})

test('shows a failed conversion stage and sanitized error', async ({ page }) => {
  let job: Record<string, unknown> | null = null
  let jobReads = 0
  const queued = { job_id: 'job-failed-1', status: 'queued', phase: 'Waiting for preparation worker', repository: 'openai/whisper-small', model_key: 'whisper-small', revision: '1a2b3c4d', operation: 'convert', runtime: 'ctranslate2', precision: 'int8', created_at: '2026-10-17T00:00:00Z', updated_at: '2026-10-17T00:00:00Z', error: null, result: null }
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', async route => {
    if (route.request().method() === 'POST') {
      job = queued
      await route.fulfill({ status: 202, json: job })
      return
    }
    if (job && ++jobReads >= 1) job = { ...queued, status: 'failed', phase: 'Inference verification failed', error: 'The local runtime could not verify this model.' }
    await route.fulfill({ json: { jobs: job ? [job] : [] } })
  })
  await page.route('**/api/v1/models/inspect', route => route.fulfill({ json: inspection('openai/whisper-tiny') }))

  await page.goto('/models')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await page.getByRole('button', { name: 'Download and convert' }).click()
  await expect(page.getByText('The local runtime could not verify this model.')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.models-job-status.failed')).toBeVisible()
})

test('ignores a stale inspection response and rejects an unsafe local key', async ({ page }) => {
  let releaseStale: (() => void) | undefined
  let finishStale: (() => void) | undefined
  const staleFinished = new Promise<void>(resolve => { finishStale = resolve })
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/inspect', async route => {
    const body = route.request().postDataJSON() as { repository: string }
    if (body.repository === 'openai/whisper-tiny') {
      await new Promise<void>(resolve => { releaseStale = resolve })
      await route.fulfill({ json: inspection(body.repository) })
      finishStale?.()
      return
    }
    await route.fulfill({ json: inspection(body.repository) })
  })

  await page.goto('/models')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await page.locator('#model-repository').fill('example/other-whisper')
  await page.getByRole('button', { name: /Inspect repository/ }).click()
  await expect(page.locator('.models-source-meta')).toContainText('example/other-whisper')
  releaseStale?.()
  await staleFinished
  await expect(page.locator('.models-source-meta')).toContainText('example/other-whisper')
  await page.locator('#model-key').fill('whisper..unsafe')
  await expect(page.getByText("without '..'", { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download source' })).toBeDisabled()
})

test('fits a narrow phone viewport without horizontal page overflow', async ({ page }) => {
  await page.route('**/api/v1/models', route => route.fulfill({ json: modelCatalog() }))
  await page.route('**/api/v1/models/jobs', route => route.fulfill({ json: emptyJobs }))
  await page.route('**/api/v1/models/not-prepared/deletion', route => route.fulfill({ json: deletionPlan('not-prepared') }))
  await page.goto('/models')
  await expect(page.getByRole('heading', { name: 'Models', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 375, height: 812 })
  await page.locator('.models-catalog-item').nth(3).getByRole('button', { name: 'Delete local model' }).click()
  await expect(page.getByRole('region', { name: 'Delete confirmation for not-prepared' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
})
