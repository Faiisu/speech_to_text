import { expect, test } from '@playwright/test'
import { Buffer } from 'node:buffer'

function pcm16Wav(seconds = 1) {
  const sampleRate = 16_000
  const data = new Uint8Array(sampleRate * seconds * 2).fill(0x20)
  const wav = new Uint8Array(44 + data.length)
  const view = new DataView(wav.buffer)
  const text = (offset: number, value: string) => [...value].forEach((char, index) => { wav[offset + index] = char.charCodeAt(0) })
  text(0, 'RIFF'); view.setUint32(4, 36 + data.length, true); text(8, 'WAVE')
  text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true)
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, data.length, true)
  wav.set(data, 44)
  return Buffer.from(wav)
}

test('records a reusable WAV and compares real clip API trials with immutable settings', async ({ page }) => {
  await page.goto('/model-tuning')
  await expect(page.getByRole('heading', { name: 'Model tuning' })).toBeVisible()
  await expect(page.getByText('MODEL READY', { exact: true })).toBeVisible()
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByRole('button', { name: /Start recording/ }).click()
  await expect(page.getByText(/Recording ·/)).toBeVisible()
  await page.getByRole('button', { name: /Stop recording/ }).click()
  await expect(page.locator('.recording-status.completed')).toBeVisible()

  const recordingId = await page.getByLabel('Choose a clip').inputValue()
  const audioResponse = await page.request.get(`/api/v1/recordings/${recordingId}/audio`)
  expect(audioResponse.ok()).toBeTruthy()
  expect(audioResponse.headers()['content-type']).toContain('audio/wav')
  expect((await audioResponse.body()).subarray(0, 4).toString()).toBe('RIFF')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: /Download WAV/ }).click()
  expect((await download).suggestedFilename()).toContain('.wav')

  await page.getByLabel('Keywords').fill('สวัสดี')
  await expect(page.getByLabel('Language')).toHaveValue('th')
  await page.getByLabel('Noise floor (RMS silence threshold)').fill('0.12')
  await page.getByLabel('Beam size').fill('4')
  await page.getByLabel('Temperature').fill('0.2')
  await page.getByLabel('Condition on previous text').check()
  await page.getByLabel('No-repeat n-gram size').fill('6')
  await page.getByLabel('Repetition penalty').fill('1.3')
  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  const first = page.locator('.tuning-trial-card').first()
  await expect(first).toContainText('completed', { timeout: 10_000 })
  await expect(first.locator('.trial-transcript')).toContainText('สวัสดีครับ')
  await first.locator('details').locator('summary').click()
  await expect(first.locator('.trial-setting-values')).toContainText('beam size')
  await expect(first.locator('.trial-setting-values')).toContainText('4')
  await expect(first.locator('.trial-setting-values')).toContainText('0.12')
  await expect(first.locator('.trial-result-grid')).toContainText('LATEST CHUNK RTF')

  await page.locator('.tuning-config-grid label').filter({ hasText: /^Runtime/ }).locator('select').selectOption('openvino-cpu')
  await page.getByLabel('Language').selectOption('en')
  await page.getByLabel('Noise floor (RMS silence threshold)').fill('0.07')
  await page.getByLabel('Beam size').fill('2')
  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  await expect(page.locator('.tuning-trial-card').first()).toContainText('completed', { timeout: 10_000 })
  const second = page.locator('.tuning-trial-card').first()
  await second.locator('details').locator('summary').click()
  await expect(second.locator('.trial-setting-values')).toContainText('openvino-cpu')
  await expect(second.locator('.trial-setting-values')).toContainText('English')
  await expect(second.locator('.trial-transcript')).toContainText('Hello there')
  await expect(second.locator('.trial-setting-values')).toContainText('0.07')
  await expect(second.locator('.trial-setting-values')).toContainText('2')
  await expect(page.locator('.tuning-trial-card').nth(1).locator('.trial-setting-values')).toContainText('turbo')
  await expect(page.locator('.tuning-trial-card').nth(1).locator('.trial-setting-values')).toContainText('Thai')
  await expect(page.locator('.tuning-trial-card').nth(1).locator('.trial-setting-values')).toContainText('4')

  const clips = await page.request.get('/api/v1/transcriptions').then(response => response.json())
  const clipRuns = clips.transcriptions.filter((run: { kind: string }) => run.kind === 'clip')
  expect(clipRuns).toHaveLength(2)
  expect(clipRuns[1]).toMatchObject({ model: 'turbo', runtime: 'openvino-gpu', language: 'th', silence_threshold: 0.12, keywords: ['สวัสดี'], decoding_options: { beam_size: 4, temperature: 0.2, condition_on_previous_text: true, no_repeat_ngram_size: 6, repetition_penalty: 1.3 } })
  expect(clipRuns[0]).toMatchObject({ model: 'turbo', runtime: 'openvino-cpu', language: 'en', silence_threshold: 0.07, decoding_options: { beam_size: 2 } })
})

test('shows every inferred chunk RTF including a short final chunk and explicit silence state', async ({ page }) => {
  await page.goto('/model-tuning')
  await expect(page.getByRole('heading', { name: 'Model tuning' })).toBeVisible()
  const audioInput = page.getByLabel('Audio file')
  const recordingOptions = page.getByLabel('Choose a clip').locator('option')
  const initialOptions = await recordingOptions.count()
  await audioInput.setInputFiles({ name: 'sixty-one-seconds.wav', mimeType: 'audio/wav', buffer: pcm16Wav(61) })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(recordingOptions).toHaveCount(initialOptions + 1)
  await page.getByLabel('Noise floor (RMS silence threshold)').fill('0')
  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  const longTrial = page.locator('.tuning-trial-card').first()
  await expect(longTrial).toContainText('completed', { timeout: 15_000 })
  const rows = longTrial.locator('.trial-measurements tbody tr')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('1')
  await expect(rows.nth(1)).toContainText('2')
  await expect(rows.nth(2)).toContainText('3')
  await expect(rows.nth(2)).toContainText('1.00 s')
  await expect(rows.nth(0).locator('td').nth(3)).not.toHaveText('—')
  await expect(rows.nth(1).locator('td').nth(3)).not.toHaveText('—')
  await expect(rows.nth(2).locator('td').nth(3)).not.toHaveText('—')

  const beforeSilence = await recordingOptions.count()
  await audioInput.setInputFiles({ name: 'silence.wav', mimeType: 'audio/wav', buffer: pcm16Wav(1).fill(0, 44) })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(recordingOptions).toHaveCount(beforeSilence + 1)
  await page.getByLabel('Noise floor (RMS silence threshold)').fill('0.01')
  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  const silenceTrial = page.locator('.tuning-trial-card').first()
  await expect(silenceTrial).toContainText('completed', { timeout: 15_000 })
  await expect(silenceTrial.locator('.trial-measurements')).toContainText('No chunks reached inference.')
  await expect(page.locator('.tuning-trial-card').nth(1).locator('.trial-measurements tbody tr')).toHaveCount(3)
})

test('keeps incremental chunk measurements distinct and preserves failed null RTF', async ({ page }) => {
  await page.goto('/model-tuning')
  await expect(page.getByRole('heading', { name: 'Model tuning' })).toBeVisible()
  const options = page.getByLabel('Choose a clip').locator('option')
  const initialOptions = await options.count()
  await page.getByLabel('Audio file').setInputFiles({ name: 'cursor-sample.wav', mimeType: 'audio/wav', buffer: pcm16Wav() })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(options).toHaveCount(initialOptions + 1)

  let statusReads = 0
  let eventReads = 0
  await page.route('**/api/v1/transcriptions/*', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    statusReads += 1
    const workflowId = new URL(route.request().url()).pathname.split('/').at(-1)!
    return route.fulfill({ json: {
      workflow_id: workflowId, kind: 'clip', status: statusReads === 1 ? 'running' : 'completed',
      transcript: 'hello', matches: [], latest_rtf: 0.43, model: 'turbo', runtime: 'openvino-gpu',
      language: 'th', silence_threshold: 0, keywords: ['สวัสดี'], decoding_options: {}, error: null,
    } })
  })
  await page.route('**/api/v1/transcriptions/*/events*', async route => {
    eventReads += 1
    const after = Number(new URL(route.request().url()).searchParams.get('after'))
    const common = { type: 'measurement', audio_seconds: 30, queue_wait_seconds: 0.02, inference_seconds: 3, timestamp: '2026-10-19T00:00:00Z' }
    const events = after === 0
      ? [{ ...common, cursor: 3, sequence: 0, rtf: 0.1, status: 'completed', error: null }]
      : [
          { ...common, cursor: 4, sequence: 1, rtf: null, status: 'failed', error: 'decoder unavailable' },
          { ...common, cursor: 5, sequence: 2, audio_seconds: 1, rtf: 0.43, status: 'completed', error: null },
        ]
    return route.fulfill({ json: { workflow_id: 'mock-workflow', events, next_cursor: events.at(-1)?.cursor ?? after } })
  })

  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  const trial = page.locator('.tuning-trial-card').first()
  await expect(trial).toContainText('completed', { timeout: 8_000 })
  const rows = trial.locator('.trial-measurements tbody tr')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0).locator('td').nth(3)).toHaveText('0.100')
  await expect(rows.nth(1).locator('td').nth(3)).toHaveText('—')
  await expect(rows.nth(1).locator('td').nth(4)).toContainText('failed · decoder unavailable')
  await expect(rows.nth(2).locator('td').nth(3)).toHaveText('0.430')
  expect(statusReads).toBeGreaterThanOrEqual(2)
  expect(eventReads).toBeGreaterThanOrEqual(2)
})

test('imports WAV files into saved recordings, preserves bytes, and reuses them after reload', async ({ page }) => {
  await page.goto('/model-tuning')
  await expect(page.getByRole('heading', { name: 'Model tuning' })).toBeVisible()

  const recordingList = page.getByLabel('Choose a clip')
  const initialCount = await recordingList.locator('option').count()
  const audioInput = page.getByLabel('Audio file')
  await audioInput.setInputFiles({ name: 'invalid.wav', mimeType: 'audio/wav', buffer: Buffer.from('not a WAV') })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(page.getByRole('alert')).toContainText(/WAV|format|audio/i)
  await expect(recordingList.locator('option')).toHaveCount(initialCount)

  const original = pcm16Wav()
  await audioInput.setInputFiles({ name: 'browser-sample.wav', mimeType: 'audio/wav', buffer: original })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(recordingList).toContainText('browser-sample.wav')
  const firstId = await recordingList.inputValue()
  const response = await page.request.get(`/api/v1/recordings/${firstId}/audio`)
  expect(response.ok()).toBeTruthy()
  expect(await response.body()).toEqual(original)
  await expect(page.locator('.recording-details')).toContainText('browser-sample.wav')

  await audioInput.setInputFiles({ name: 'browser-sample.wav', mimeType: 'audio/wav', buffer: original })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(recordingList.locator('option')).toHaveCount(initialCount + 2)
  const secondId = await recordingList.inputValue()
  expect(secondId).not.toBe(firstId)

  await page.reload()
  await expect(recordingList).toHaveValue(secondId)
  await expect(recordingList).toContainText('browser-sample.wav')
  await page.getByLabel('Language').selectOption('en')
  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  const trial = page.locator('.tuning-trial-card').first()
  await expect(trial).toContainText('completed', { timeout: 10_000 })
  await expect(trial.locator('.trial-card-title')).toContainText('browser-sample.wav')
  await expect(trial.locator('.trial-transcript')).toContainText('Hello there')
})

test('records IPCAM through the bounded recording workflow when microphone discovery is unavailable', async ({ page }) => {
  await page.route('**/api/v1/microphones', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Microphone discovery unavailable' }),
  }))
  await page.goto('/model-tuning')
  await expect(page.getByRole('heading', { name: 'Model tuning' })).toBeVisible()
  await expect(page.getByText('MODEL READY', { exact: true })).toBeVisible()

  await page.getByLabel('Input source').selectOption('rtsp')
  await page.getByLabel('RTSP URL').fill('rtsp://camera.local:554/audio')
  await page.getByRole('button', { name: 'Start recording' }).click()
  await expect(page.getByText(/Recording ·/)).toBeVisible()
  const recordingId = await page.getByLabel('Choose a clip').inputValue()
  await page.getByRole('button', { name: 'Stop recording' }).click()
  await expect(page.locator('.recording-status.completed')).toBeVisible()
  await expect(page.getByLabel('Choose a clip')).toContainText('IPCAM (RTSP)')
  await expect(page.locator('.recording-details')).toContainText('IPCAM (RTSP)')

  const audioResponse = await page.request.get(`/api/v1/recordings/${recordingId}/audio`)
  expect(audioResponse.ok()).toBeTruthy()
  expect((await audioResponse.body()).subarray(0, 4).toString()).toBe('RIFF')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: /Download WAV/ }).click()
  expect((await download).suggestedFilename()).toContain('.wav')

  await page.getByRole('button', { name: /Run tuning trial/ }).click()
  const trial = page.locator('.tuning-trial-card').first()
  await expect(trial).toContainText('completed', { timeout: 10_000 })
  await expect(trial.locator('.trial-card-title')).toContainText('IPCAM (RTSP)')
  await expect(trial.locator('.trial-transcript')).toContainText('สวัสดีครับ')

  await page.getByLabel('Input source').selectOption('rtsp')
  await page.getByLabel('RTSP address source').selectOption('secret_ref')
  await page.getByLabel('Environment variable name').fill('E2E_CAMERA_RTSP_URL')
  await page.getByRole('button', { name: 'Start recording' }).click()
  await expect(page.getByText(/Recording ·/)).toBeVisible()
  const secretRecordingId = await page.getByLabel('Choose a clip').inputValue()
  await page.getByRole('button', { name: 'Stop recording' }).click()
  await expect(page.locator('.recording-status.completed')).toBeVisible()
  const snapshot = await page.request.get(`/api/v1/recordings/${secretRecordingId}`).then(response => response.json())
  expect(snapshot).toMatchObject({ source: 'rtsp', device: null, rtsp_url: null, rtsp_url_secret_ref: 'E2E_CAMERA_RTSP_URL' })
  expect(JSON.stringify(snapshot)).not.toContain('camera-e2e-password')
})

test('model tuning accepts direct RTSP credentials without persisting them in recording metadata', async ({ page }) => {
  const password = 'tuning-p@ss:/%#'
  await page.goto('/model-tuning')
  await page.getByLabel('Input source').selectOption('rtsp')
  await page.getByLabel('RTSP URL').fill('rtsp://camera.example/audio')
  await page.getByLabel('Username').fill('tuning-user')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Start recording' }).click()
  await expect(page.getByText(/Recording ·/)).toBeVisible()
  const recordingId = await page.getByLabel('Choose a clip').inputValue()
  await page.getByRole('button', { name: 'Stop recording' }).click()
  await expect(page.locator('.recording-status.completed')).toBeVisible()
  const snapshot = await page.request.get(`/api/v1/recordings/${recordingId}`).then(response => response.json())
  expect(snapshot).toMatchObject({ source: 'rtsp', rtsp_url: 'rtsp://camera.example/audio', rtsp_username: 'tuning-user', rtsp_has_password: true })
  expect(JSON.stringify(snapshot)).not.toContain(password)
  await expect(page.locator('body')).not.toContainText(password)
})
