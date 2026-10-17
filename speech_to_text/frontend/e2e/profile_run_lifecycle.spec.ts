import { expect, test } from '@playwright/test'

test('IPCAM profiles stay usable when microphone discovery is unavailable', async ({ page }) => {
  await page.route('**/api/v1/microphones', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Microphone discovery is unavailable.' }),
  }))

  await page.goto('/profiles/new')
  await page.getByLabel('Name', { exact: true }).fill('E2E IPCAM profile')
  await page.getByLabel('Input source').selectOption('rtsp')
  await page.getByLabel('RTSP URL').fill('rtsp://camera.example/live')
  await page.getByLabel('Model', { exact: true }).selectOption('small')
  await page.getByLabel('One keyword per line').fill('กล้อง')
  await expect(page.getByRole('button', { name: /Save profile/ })).toBeEnabled()
  await page.getByRole('button', { name: /Save profile/ }).click()

  await expect(page).toHaveURL(/\/profiles$/)
  const original = page.locator('.profile-record').filter({ hasText: 'E2E IPCAM profile' })
  await expect(original).toContainText('IPCAM (RTSP)')
  await original.getByRole('link', { name: 'Edit' }).click()
  await expect(page.getByLabel('Input source')).toHaveValue('rtsp')
  await expect(page.getByLabel('RTSP URL')).toHaveValue('rtsp://camera.example/live')
  await page.getByLabel('RTSP URL').fill('rtsp://camera.example/edited')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.locator('.profile-record').filter({ hasText: 'E2E IPCAM profile' })).toContainText('IPCAM (RTSP)')

  const profile = await page.request.get('/api/v1/profiles').then(async response => {
    const payload = await response.json()
    return payload.profiles.find((item: { name: string }) => item.name === 'E2E IPCAM profile')
  })
  expect(profile).toMatchObject({
    source_type: 'rtsp',
    rtsp_url: 'rtsp://camera.example/edited',
    rtsp_url_secret_ref: null,
    device: null,
  })

  await page.goto(`/profiles/new?duplicate=${profile.profile_id}`)
  await expect(page.getByLabel('Input source')).toHaveValue('rtsp')
  await expect(page.getByLabel('RTSP URL')).toHaveValue('rtsp://camera.example/edited')
  await page.getByLabel('Name', { exact: true }).fill('E2E IPCAM duplicate')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.locator('.profile-record').filter({ hasText: 'E2E IPCAM duplicate' })).toContainText('IPCAM (RTSP)')
  const duplicate = await page.request.get('/api/v1/profiles').then(async response => {
    const payload = await response.json()
    return payload.profiles.find((item: { name: string }) => item.name === 'E2E IPCAM duplicate')
  })

  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('IPCAM profiles remain available')
  await page.getByRole('button', { name: 'Start workflow E2E IPCAM duplicate' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  const workflowId = page.url().split('/').at(-1)!
  const run = await page.request.get(`/api/v1/transcriptions/${workflowId}`).then(response => response.json())
  expect(run).toMatchObject({ source_type: 'rtsp', kind: 'microphone', device: null, profile_name: 'E2E IPCAM duplicate' })
  await expect(page.getByRole('heading', { name: 'E2E IPCAM duplicate' })).toBeVisible()
  await expect(page.locator('.run-heading')).toContainText('IPCAM (RTSP)')
  await expect(page.getByRole('heading', { name: 'Matched keywords' })).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Stop listening' }).click()
  await expect.poll(async () => page.request.get(`/api/v1/transcriptions/${workflowId}`).then(async response => (await response.json()).status)).toBe('stopped')
  await page.request.delete(`/api/v1/profiles/${profile.profile_id}`)
  await page.request.delete(`/api/v1/profiles/${duplicate.profile_id}`)
})

test('RTSP secret references can be created, edited, and reloaded without exposing the resolved URL', async ({ page }) => {
  const resolvedUrl = 'rtsp://camera-e2e-user:camera-e2e-password@camera.example/secret'
  await page.goto('/profiles/new')
  await page.getByLabel('Name', { exact: true }).fill('E2E secret IPCAM profile')
  await page.getByLabel('Input source').selectOption('rtsp')
  await page.getByLabel('RTSP address source').selectOption('secret')
  await page.getByLabel('Environment variable name').fill('E2E_CAMERA_RTSP_URL')
  await page.getByLabel('Model', { exact: true }).selectOption('small')
  await page.getByLabel('One keyword per line').fill('กล้องลับ')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)
  await expect(page.locator('body')).not.toContainText(resolvedUrl)
  await expect(page.locator('body')).not.toContainText('camera-e2e-password')

  const profile = await page.request.get('/api/v1/profiles').then(async response => {
    const payload = await response.json()
    return payload.profiles.find((item: { name: string }) => item.name === 'E2E secret IPCAM profile')
  })
  expect(profile).toMatchObject({
    source_type: 'rtsp',
    rtsp_url: null,
    rtsp_url_secret_ref: 'E2E_CAMERA_RTSP_URL',
    device: null,
  })
  expect(JSON.stringify(profile)).not.toContain(resolvedUrl)
  expect(JSON.stringify(profile)).not.toContain('camera-e2e-password')

  await page.goto(`/profiles/${profile.profile_id}`)
  await expect(page.getByLabel('Environment variable name')).toHaveValue('E2E_CAMERA_RTSP_URL')
  await page.getByLabel('Name', { exact: true }).fill('E2E edited secret IPCAM profile')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)
  const edited = await page.request.get(`/api/v1/profiles/${profile.profile_id}`).then(response => response.json())
  expect(edited).toMatchObject({ name: 'E2E edited secret IPCAM profile', rtsp_url: null, rtsp_url_secret_ref: 'E2E_CAMERA_RTSP_URL' })
  expect(JSON.stringify(edited)).not.toContain(resolvedUrl)

  await page.goto(`/profiles/${profile.profile_id}`)
  await expect(page.getByLabel('Environment variable name')).toHaveValue('E2E_CAMERA_RTSP_URL')
  await expect(page.locator('body')).not.toContainText(resolvedUrl)
  await expect(page.locator('body')).not.toContainText('camera-e2e-password')
  await page.request.delete(`/api/v1/profiles/${profile.profile_id}`)
})

test('user can create a profile, monitor its output, and stop the workflow', async ({ page }) => {
  await page.goto('/profiles/new')
  await expect(page.locator('.brand')).toContainText('EchoDesk')
  await expect(page.locator('.brand')).toContainText('AUDIO TRANSCRIPTION')
  await expect(page.locator('.crumb')).toContainText('ECHODESK')

  await page.getByLabel('Name', { exact: true }).fill('E2E studio profile')
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByLabel('Model', { exact: true }).selectOption('small')
  await page.getByLabel('Runtime').selectOption('ctranslate2')
  await expect(page.getByLabel('Language')).toHaveValue('th')
  await page.getByLabel('Language').selectOption('en')
  await expect(page.getByLabel('Runtime')).toContainText('unavailable')
  await page.getByLabel('One keyword per line').fill('สวัสดี')
  await page.getByRole('button', { name: /Save profile/ }).click()

  await expect(page).toHaveURL(/\/profiles$/)
  await expect(page.getByRole('heading', { name: 'E2E studio profile' })).toBeVisible()
  await expect(page.getByText('Model: small · ctranslate2')).toBeVisible()

  await page.locator('.profile-record').filter({ hasText: 'E2E studio profile' }).getByRole('link', { name: 'Edit' }).click()
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('small')
  await expect(page.getByLabel('Runtime')).toHaveValue('ctranslate2')
  await expect(page.getByLabel('Language')).toHaveValue('en')
  await page.getByLabel('Runtime').selectOption('openvino-cpu')
  await page.getByLabel('Model', { exact: true }).selectOption('tiny')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.getByText('Model: tiny · openvino-cpu')).toBeVisible()

  await page.goto('/')
  await page.getByRole('button', { name: 'Start workflow E2E studio profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  await expect(page.locator('.run-kpis')).toContainText('tiny')
  await expect(page.locator('.run-kpis')).toContainText('openvino-cpu')

  await expect(page.locator('.match-list')).toContainText('สวัสดี', { timeout: 10_000 })
  await expect(page.locator('.match-list')).toContainText('สวัสดี')
  await expect(page.locator('.match-list')).toContainText('1 matches')

  await page.getByRole('link', { name: 'Back to overview' }).click()
  await page.getByRole('link', { name: 'Audio inputs & profiles' }).click()
  const profileRecord = page.locator('.profile-record').filter({ hasText: 'E2E studio profile' })
  await expect(profileRecord.getByRole('button', { name: 'Stop workflow E2E studio profile' })).toBeVisible()
  await page.getByRole('link', { name: 'Overview' }).click()
  const workflowRow = page.locator('.run-row').filter({ hasText: 'E2E studio profile' })
  await workflowRow.getByRole('button', { name: 'Stop workflow E2E studio profile' }).click()
  await expect(workflowRow).toHaveCount(0, { timeout: 10_000 })
})

test('profile Play becomes Stop by profile identity, even when another profile uses the same model', async ({ page }) => {
  const definition = (name: string) => ({
    name,
    device: 'E2E Studio Mic',
    execution_mode: 'shared',
    model: 'small',
    runtime: 'openvino-gpu',
    keywords: ['ทดสอบ'],
    silence_threshold: 0,
  })
  await page.request.post('/api/v1/profiles', { data: definition('Alpha profile') })
  await page.request.post('/api/v1/profiles', { data: definition('Beta profile') })
  await page.goto('/profiles')

  await page.getByRole('button', { name: 'Start workflow Alpha profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  await page.getByRole('link', { name: 'Audio inputs & profiles' }).click()

  const alpha = page.locator('.profile-record').filter({ hasText: 'Alpha profile' })
  const beta = page.locator('.profile-record').filter({ hasText: 'Beta profile' })
  const stop = alpha.getByRole('button', { name: 'Stop workflow Alpha profile' })
  await expect(stop).toBeVisible()
  await expect(beta.getByRole('button', { name: 'Start workflow Beta profile' })).toBeVisible()

  let releaseStop: (() => void) | undefined
  let stopRequestStarted: (() => void) | undefined
  const stopGate = new Promise<void>(resolve => { releaseStop = resolve })
  const stopSignal = new Promise<void>(resolve => { stopRequestStarted = resolve })
  await page.route('**/api/v1/transcriptions/*/stop', async route => {
    stopRequestStarted?.()
    await stopGate
    await route.continue()
  })
  await stop.click()
  await stopSignal
  await expect(stop).toContainText('Stopping…')
  await expect(stop).toBeDisabled()
  releaseStop?.()
  await expect(alpha.getByRole('button', { name: 'Start workflow Alpha profile' })).toBeVisible({ timeout: 10_000 })
  await expect(beta.getByRole('button', { name: 'Start workflow Beta profile' })).toBeVisible()
})

test('one profile reuses its active workflow instead of starting another process', async ({ page }) => {
  const response = await page.request.post('/api/v1/profiles', { data: {
    name: 'Single active profile',
    device: 'E2E Studio Mic',
    execution_mode: 'shared',
    model: 'small',
    runtime: 'openvino-gpu',
    keywords: ['ทดสอบ'],
    silence_threshold: 0,
  } })
  const profile = await response.json()
  const firstStart = await page.request.post(`/api/v1/profiles/${profile.profile_id}/runs`).then(result => result.json())
  const secondStart = await page.request.post(`/api/v1/profiles/${profile.profile_id}/runs`).then(result => result.json())
  expect(secondStart).toEqual(firstStart)
  const activeRuns = await page.request.get('/api/v1/transcriptions').then(async result => (await result.json()).transcriptions.filter((run: { profile_id: string; status: string }) => run.profile_id === profile.profile_id && ['queued', 'running', 'recording', 'stopping'].includes(run.status)))
  expect(activeRuns).toHaveLength(1)
  await page.goto('/profiles')

  const row = page.locator('.profile-record').filter({ hasText: 'Single active profile' })
  const stop = row.getByRole('button', { name: 'Stop workflow Single active profile' })
  let stopRequests = 0
  await page.route('**/api/v1/transcriptions/*/stop', async route => {
    stopRequests += 1
    await route.continue()
  })
  await stop.click()
  await expect(row.getByRole('button', { name: 'Start workflow Single active profile' })).toBeVisible({ timeout: 10_000 })
  expect(stopRequests).toBe(1)
})

test('overview shows profile state and stops the existing workflow from its profile card', async ({ page }) => {
  const response = await page.request.post('/api/v1/profiles', { data: {
    name: 'Overview state profile',
    device: 'E2E Studio Mic',
    execution_mode: 'shared',
    model: 'small',
    runtime: 'openvino-gpu',
    keywords: ['ทดสอบ'],
    silence_threshold: 0,
  } })
  const profile = await response.json()
  await page.request.post(`/api/v1/profiles/${profile.profile_id}/runs`)
  await page.goto('/')

  const profileCard = page.locator('.profile-card').filter({ hasText: 'Overview state profile' })
  const stop = profileCard.getByRole('button', { name: 'Stop workflow Overview state profile' })
  await expect(stop).toBeVisible()
  await expect(page.getByText('Active profiles')).toBeVisible()
  await stop.click()
  await expect(profileCard.getByRole('button', { name: 'Start workflow Overview state profile' })).toBeVisible({ timeout: 10_000 })
})

test('profile Stop returns to Play when an accepted shutdown later fails', async ({ page }) => {
  await page.request.post('/api/v1/profiles', { data: {
    name: 'Failed stop profile',
    device: 'E2E Studio Mic',
    execution_mode: 'shared',
    model: 'small',
    runtime: 'openvino-gpu',
    keywords: ['ทดสอบ'],
    silence_threshold: 0,
  } })
  await page.goto('/profiles')
  await page.getByRole('button', { name: 'Start workflow Failed stop profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  await page.getByRole('link', { name: 'Audio inputs & profiles' }).click()

  let shutdownFailed = false
  await page.route('**/api/v1/transcriptions', async route => {
    const response = await route.fetch()
    const payload = await response.json()
    if (shutdownFailed) {
      payload.transcriptions = payload.transcriptions.map((run: { profile_name: string; status: string }) => (
        run.profile_name === 'Failed stop profile' ? { ...run, status: 'failed', error: 'capture device refused to stop' } : run
      ))
    }
    await route.fulfill({ response, body: JSON.stringify(payload) })
  })
  await page.route('**/api/v1/transcriptions/*/stop', async route => {
    shutdownFailed = true
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ workflow_id: 'failed-stop-workflow', status: 'stopping' }),
    })
  })

  const profile = page.locator('.profile-record').filter({ hasText: 'Failed stop profile' })
  await profile.getByRole('button', { name: 'Stop workflow Failed stop profile' }).click()
  await expect(profile.getByRole('button', { name: 'Start workflow Failed stop profile' })).toBeVisible()
})

test('profile Play starts its saved snapshot and opens the workflow', async ({ page }) => {
  await page.goto('/profiles/new')
  await page.getByLabel('Name', { exact: true }).fill('Play snapshot profile')
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByLabel('Model', { exact: true }).selectOption('small')
  await page.getByLabel('Runtime').selectOption('ctranslate2')
  await page.getByLabel('One keyword per line').fill('สวัสดี\nประชุม')
  await page.getByLabel('Noise floor (RMS silence threshold)').fill('0.17')
  await page.getByLabel('Beam size').fill('4')
  await page.getByLabel('Temperature').fill('0.25')
  await page.getByLabel('Condition on previous text').check()
  await page.getByLabel('No-repeat n-gram size').fill('6')
  await page.getByLabel('Repetition penalty').fill('1.4')
  await page.getByLabel('Separate process').check()
  await page.getByRole('button', { name: /Save profile/ }).click()

  await expect(page).toHaveURL(/\/profiles$/)
  await page.getByRole('button', { name: 'Start workflow Play snapshot profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  const workflowId = page.url().split('/').at(-1)!
  const run = await page.request.get(`/api/v1/transcriptions/${workflowId}`).then(response => response.json())
  expect(run).toMatchObject({
    profile_name: 'Play snapshot profile',
    device: 'E2E Studio Mic',
    execution_mode: 'per_workflow_process',
    model: 'small',
    runtime: 'ctranslate2',
    keywords: ['สวัสดี', 'ประชุม'],
    silence_threshold: 0.17,
  })
  await expect(page.locator('.run-kpis')).toContainText('small')
  await expect(page.locator('.run-kpis')).toContainText('ctranslate2')
  await expect(page.locator('.run-kpis')).toContainText('Separate process')
  await expect(page.getByRole('heading', { name: 'Play snapshot profile' })).toBeVisible()
  await expect(page.locator('.match-list')).toContainText('สวัสดี')
})

test('profile Play prevents duplicate starts while pending and can retry a failed start', async ({ page }) => {
  await page.goto('/profiles/new')
  await page.getByLabel('Name', { exact: true }).fill('Play retry profile')
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByLabel('One keyword per line').fill('ทดสอบ')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)

  let requestCount = 0
  let releaseFirstRequest: (() => void) | undefined
  let firstRequestStarted: (() => void) | undefined
  const firstRequestGate = new Promise<void>(resolve => { releaseFirstRequest = resolve })
  const firstRequestSignal = new Promise<void>(resolve => { firstRequestStarted = resolve })
  await page.route('**/api/v1/profiles/*/runs', async route => {
    requestCount += 1
    if (requestCount === 1) {
      firstRequestStarted?.()
      await firstRequestGate
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'The selected model runtime is unavailable.' }) })
      return
    }
    await route.continue()
  })

  const play = page.getByRole('button', { name: 'Start workflow Play retry profile' })
  await play.click()
  await firstRequestSignal
  await expect(play).toHaveText('Starting…')
  await expect(play).toBeDisabled()
  expect(requestCount).toBe(1)
  await releaseFirstRequest?.()

  await expect(page.getByRole('alert')).toContainText('Could not start workflow for “Play retry profile”. The selected model runtime is unavailable.')
  await expect(page.getByRole('alert')).not.toContainText('Cannot reach the API')
  await expect(play).toBeEnabled()
  await play.click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  expect(requestCount).toBe(2)
  await expect(page.getByRole('heading', { name: 'Play retry profile' })).toBeVisible()
})

test('duplicate profile copies settings into a reload-safe new profile draft', async ({ page }) => {
  await page.goto('/profiles/new')
  await page.getByLabel('Name', { exact: true }).fill('Source profile')
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByLabel('Model', { exact: true }).selectOption('small')
  await page.getByLabel('Runtime').selectOption('ctranslate2')
  await expect(page.getByLabel('Language')).toHaveValue('th')
  await page.getByLabel('Language').selectOption('en')
  await page.getByLabel('One keyword per line').fill('สวัสดี\nประชุม')
  await page.getByLabel('Noise floor (RMS silence threshold)').fill('0.17')
  await page.getByLabel('Separate process').check()
  await page.getByRole('button', { name: /Save profile/ }).click()

  await expect(page).toHaveURL(/\/profiles$/)
  await page.locator('.profile-record').filter({ hasText: 'Source profile' }).getByRole('link', { name: 'Edit' }).click()
  await expect(page.getByLabel('Beam size')).toHaveValue('1')
  await page.getByLabel('Beam size').fill('4')
  await page.getByLabel('Temperature').fill('0.25')
  await page.getByLabel('Condition on previous text').check()
  await page.getByLabel('No-repeat n-gram size').fill('6')
  await page.getByLabel('Repetition penalty').fill('1.4')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)
  const sourceId = await page.request.get('/api/v1/profiles').then(async response => {
    const data = await response.json()
    return data.profiles.find((profile: { name: string }) => profile.name === 'Source profile').profile_id as string
  })
  const originalBefore = await page.request.get(`/api/v1/profiles/${sourceId}`).then(response => response.json())
  const profileCountBeforeDuplicate = await page.request.get('/api/v1/profiles').then(async response => (await response.json()).profiles.length)

  const sourceRow = page.locator('.profile-record').filter({ hasText: 'Source profile' })
  for (const width of [1280, 1000, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
    await expect(sourceRow.getByRole('link', { name: 'Edit' })).toBeVisible()
    await expect(sourceRow.getByRole('link', { name: 'Duplicate profile Source profile' })).toBeVisible()
    await expect(sourceRow.getByRole('button', { name: 'Delete profile Source profile' })).toBeVisible()
    await expect(sourceRow.getByRole('button', { name: 'Start workflow Source profile' })).toBeVisible()
  }
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('link', { name: 'Duplicate profile Source profile' }).click()
  await expect(page).toHaveURL(new RegExp(`/profiles/new\\?duplicate=${sourceId}`))
  await expect.poll(async () => page.request.get('/api/v1/profiles').then(async response => (await response.json()).profiles.length)).toBe(profileCountBeforeDuplicate)
  await expect(page.getByRole('heading', { name: 'Duplicate profile' })).toBeVisible()
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('Microphone')).toHaveValue('E2E Studio Mic')
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('small')
  await expect(page.getByLabel('Runtime')).toHaveValue('ctranslate2')
  await expect(page.getByLabel('Language')).toHaveValue('en')
  await expect(page.getByLabel('One keyword per line')).toHaveValue('สวัสดี\nประชุม')
  await expect(page.getByLabel('Noise floor (RMS silence threshold)')).toHaveValue('0.17')
  await expect(page.getByLabel('Beam size')).toHaveValue('4')
  await expect(page.getByLabel('Temperature')).toHaveValue('0.25')
  await expect(page.getByLabel('Condition on previous text')).toBeChecked()
  await expect(page.getByLabel('No-repeat n-gram size')).toHaveValue('6')
  await expect(page.getByLabel('Repetition penalty')).toHaveValue('1.4')
  await expect(page.getByLabel('Separate process')).toBeChecked()

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Duplicate profile' })).toBeVisible()
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('Beam size')).toHaveValue('4')
  await expect(page.getByLabel('Temperature')).toHaveValue('0.25')
  await expect(page.getByLabel('Condition on previous text')).toBeChecked()
  await expect(page.getByLabel('No-repeat n-gram size')).toHaveValue('6')
  await expect(page.getByLabel('Repetition penalty')).toHaveValue('1.4')
  await expect(page.getByLabel('One keyword per line')).toHaveValue('สวัสดี\nประชุม')

  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.getByRole('alert')).toContainText('Enter a profile name')
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('')

  await page.getByLabel('Name', { exact: true }).fill('  SOURCE PROFILE  ')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.getByRole('alert')).toContainText('already exists')
  await expect(page).toHaveURL(new RegExp(`/profiles/new\\?duplicate=${sourceId}`))

  await page.getByLabel('Name', { exact: true }).fill('Copied profile')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)
  await expect(page.getByRole('heading', { name: 'Copied profile' })).toBeVisible()
  const profiles = await page.request.get('/api/v1/profiles').then(async response => (await response.json()).profiles)
  expect(profiles.filter((profile: { name: string }) => ['Source profile', 'Copied profile'].includes(profile.name))).toHaveLength(2)
  const copy = profiles.find((profile: { name: string }) => profile.name === 'Copied profile')
  expect(copy.profile_id).not.toBe(sourceId)
  expect(copy).toMatchObject({
    name: 'Copied profile',
    device: originalBefore.device,
    execution_mode: originalBefore.execution_mode,
    model: originalBefore.model,
    runtime: originalBefore.runtime,
    language: 'en',
    keywords: originalBefore.keywords,
    silence_threshold: originalBefore.silence_threshold,
    decoding_options: { beam_size: 4, temperature: 0.25, condition_on_previous_text: true, no_repeat_ngram_size: 6, repetition_penalty: 1.4 },
  })
  await page.getByRole('button', { name: 'Start workflow Copied profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  const copiedWorkflowId = page.url().split('/').at(-1)!
  await expect(page.locator('.run-kpis')).toContainText('English')
  await expect.poll(async () => page.request.get(`/api/v1/transcriptions/${copiedWorkflowId}`).then(response => response.json())).toMatchObject({ language: 'en', decoding_options: copy.decoding_options })
  const updateResponse = await page.request.put(`/api/v1/profiles/${copy.profile_id}`, { data: {
    name: copy.name,
    device: copy.device,
    execution_mode: copy.execution_mode,
    keywords: copy.keywords,
    silence_threshold: copy.silence_threshold,
    model: copy.model,
    runtime: copy.runtime,
    destination_id: copy.destination_id,
    decoding_options: copy.decoding_options,
    language: 'auto',
  } })
  expect(updateResponse.ok()).toBeTruthy()
  await expect.poll(async () => page.request.get(`/api/v1/transcriptions/${copiedWorkflowId}`).then(response => response.json())).toMatchObject({ language: 'en' })
  await page.getByRole('button', { name: 'Stop listening' }).click()
  await expect.poll(async () => page.request.get(`/api/v1/transcriptions/${copiedWorkflowId}`).then(async response => (await response.json()).status)).toBe('stopped')
  await page.goto('/profiles')
  await page.getByRole('button', { name: 'Start workflow Copied profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  const restartedWorkflowId = page.url().split('/').at(-1)!
  await expect.poll(async () => page.request.get(`/api/v1/transcriptions/${restartedWorkflowId}`).then(response => response.json())).toMatchObject({ language: 'auto' })
  await page.getByRole('button', { name: 'Stop listening' }).click()
  await expect.poll(async () => page.request.get(`/api/v1/transcriptions/${restartedWorkflowId}`).then(async response => (await response.json()).status)).toBe('stopped')
  const originalAfter = await page.request.get(`/api/v1/profiles/${sourceId}`).then(response => response.json())
  expect(originalAfter).toEqual(originalBefore)

  await page.request.delete(`/api/v1/profiles/${sourceId}`)
  await page.goto(`/profiles/new?duplicate=${sourceId}`)
  await expect(page.getByRole('alert')).toContainText('Could not load the profile to duplicate')
  await expect(page.getByRole('button', { name: /Save profile/ })).toBeDisabled()
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('Could not load the profile to duplicate')
  await expect(page.getByRole('button', { name: /Save profile/ })).toBeDisabled()
})

test('dashboard profile carousel exposes every profile and can navigate to the end', async ({ page }) => {
  const prefix = `Dashboard carousel ${Date.now()}`
  const names = Array.from({ length: 5 }, (_, index) => `${prefix} ${index + 1}`)
  for (const name of names) {
    const response = await page.request.post('/api/v1/profiles', { data: {
      name,
      device: 'E2E Studio Mic',
      execution_mode: 'shared',
      model: 'small',
      runtime: 'openvino-gpu',
      keywords: ['เลื่อนโปรไฟล์'],
      silence_threshold: 0,
    } })
    expect(response.ok()).toBe(true)
  }

  const allProfiles = await page.request.get('/api/v1/profiles').then(async response => (await response.json()).profiles)
  await page.goto('/')
  const carousel = page.getByRole('region', { name: 'Audio input profiles' })
  const cards = carousel.locator('.profile-card')
  await expect(cards).toHaveCount(allProfiles.length)
  for (const name of names) {
    await expect(cards.getByRole('heading', { name })).toBeAttached()
    await expect(cards.filter({ hasText: name }).getByRole('button', { name: `Start workflow ${name}` })).toBeAttached()
  }

  const previous = page.getByRole('button', { name: 'Previous profiles' })
  const next = page.getByRole('button', { name: 'Next profiles' })
  await expect(previous).toBeDisabled()
  await expect(next).toBeEnabled()
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)

  const reached = new Set<string>()
  const collectVisibleProfiles = async () => {
    const visibleNames = await page.evaluate(() => {
      const track = document.querySelector<HTMLElement>('#dashboard-profile-carousel')!
      const trackBounds = track.getBoundingClientRect()
      return [...track.querySelectorAll<HTMLElement>('.profile-card')]
        .filter(card => {
          const bounds = card.getBoundingClientRect()
          return bounds.right > trackBounds.left && bounds.left < trackBounds.right
        })
        .map(card => card.querySelector('h3')?.textContent ?? '')
    })
    visibleNames.forEach(name => reached.add(name))
  }
  await collectVisibleProfiles()
  while (await next.isEnabled()) {
    await next.click()
    await expect.poll(async () => page.evaluate(() => {
      const track = document.querySelector<HTMLElement>('#dashboard-profile-carousel')!
      return Math.round(track.scrollLeft)
    })).toBeGreaterThan(0)
    await page.evaluate(async () => {
      const track = document.querySelector<HTMLElement>('#dashboard-profile-carousel')!
      let previousLeft = track.scrollLeft
      let stableFrames = 0
      while (stableFrames < 3) {
        await new Promise(resolve => window.setTimeout(resolve, 80))
        const currentLeft = track.scrollLeft
        stableFrames = Math.abs(currentLeft - previousLeft) < 1 ? stableFrames + 1 : 0
        previousLeft = currentLeft
      }
    })
    await collectVisibleProfiles()
  }
  await expect(next).toBeDisabled()
  await expect(previous).toBeEnabled()
  for (const name of names) expect([...reached], `Profiles reached while scrolling: ${[...reached].join(', ')}`).toContain(name)

  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await expect(previous).toBeEnabled()
  await expect(next).toBeEnabled()
  const mobileScrollBefore = await carousel.evaluate(element => element.scrollLeft)
  await next.click()
  await expect.poll(() => carousel.evaluate(element => element.scrollLeft)).toBeGreaterThan(mobileScrollBefore)
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
})

test('stale workflow IDs explain that run history is process-local', async ({ page }) => {
  await page.goto('/runs/unknown-workflow')

  await expect(page.getByRole('status')).toContainText('This workflow is not in the current backend history.')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('singular run route keeps keyword tracking and only the latest 100 events after cursor expiry', async ({ page }) => {
  await page.route('**/api/v1/transcriptions/workspace-e2e', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      workflow_id: 'workspace-e2e', kind: 'microphone', status: 'running', keywords: ['alpha'],
      profile_id: null, profile_name: 'Workspace e2e', source_type: 'microphone', device: 'Test mic',
      execution_mode: 'shared', silence_threshold: 0, decoding_options: null, model: 'small', runtime: 'ctranslate2',
      language: 'en', transcript: 'This response contains transcript content which must not render.', matches: [{ keyword: 'alpha', count: 7 }],
      latest_rtf: 0.5, error: null, delivery: null, created_at: '2026-10-17T00:00:00Z',
    }),
  }))
  let eventRequest = 0
  await page.route('**/api/v1/transcriptions/workspace-e2e/events?after=*', async route => {
    const after = new URL(route.request().url()).searchParams.get('after')
    if (eventRequest++ === 0) {
      const events = Array.from({ length: 80 }, (_, index) => ({ cursor: index + 1, type: index === 1 ? 'transcript' : 'recording', text: 'Event transcript content must not render.', timestamp: '2026-10-17T00:00:00Z' }))
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ workflow_id: 'workspace-e2e', events, next_cursor: 80 }) })
    } else if (after === '80') {
      const events = Array.from({ length: 50 }, (_, index) => ({ cursor: index + 81, type: 'recording', timestamp: '2026-10-17T00:00:00Z' }))
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ workflow_id: 'workspace-e2e', events, next_cursor: 130 }) })
    } else if (after === '130') {
      await route.fulfill({ status: 410, contentType: 'application/json', body: JSON.stringify({ detail: { message: 'event cursor expired', oldest_cursor: 201 } }) })
    } else {
      expect(after).toBe('200')
      const events = Array.from({ length: 30 }, (_, index) => ({ cursor: index + 201, type: 'recording', timestamp: '2026-10-17T00:00:00Z' }))
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ workflow_id: 'workspace-e2e', events, next_cursor: 230 }) })
    }
  })

  await page.goto('/run/workspace-e2e')
  await expect(page.getByRole('heading', { name: 'Matched keywords' })).toBeVisible()
  await expect(page.locator('.match-list')).toContainText('alpha')
  await expect(page.locator('.match-list')).toContainText('7 matches')
  await expect(page.getByRole('heading', { name: 'Events' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('Earlier events expired', { timeout: 8_000 })
  await expect(page.locator('.event-count')).toHaveText('100')
  await expect(page.locator('.timeline')).toContainText('#230')
  await expect(page.locator('.timeline')).toContainText('#61')
  await expect(page.locator('.timeline')).not.toContainText('#31')
  await expect(page.locator('body')).not.toContainText('transcript content')
})

test('authenticated RTSP profiles keep passwords write-only and require duplicate re-entry', async ({ page }) => {
  const password = 'camera-p@ss:/%#'
  await page.goto('/profiles/new')
  await page.getByLabel('Name', { exact: true }).fill('E2E authenticated camera')
  await page.getByLabel('Input source').selectOption('rtsp')
  await page.getByLabel('RTSP URL').fill('rtsp://camera.example:554/live')
  await page.getByLabel('Username').fill('operator')
  await page.getByLabel('Password').fill(password)
  await page.getByLabel('One keyword per line').fill('กล้อง')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)

  const profile = await page.request.get('/api/v1/profiles').then(async response => {
    const payload = await response.json()
    return payload.profiles.find((item: { name: string }) => item.name === 'E2E authenticated camera')
  })
  expect(profile).toMatchObject({ rtsp_url: 'rtsp://camera.example:554/live', rtsp_username: 'operator', rtsp_has_password: true })
  expect(JSON.stringify(profile)).not.toContain(password)

  await page.goto(`/profiles/new?duplicate=${profile.profile_id}`)
  await page.getByLabel('Name', { exact: true }).fill('E2E authenticated duplicate')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.getByRole('alert')).toContainText('Re-enter the saved camera password')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)
  await expect(page.locator('body')).not.toContainText(password)
  await page.request.delete(`/api/v1/profiles/${profile.profile_id}`)
})
