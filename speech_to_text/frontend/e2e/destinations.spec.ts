import { expect, test } from '@playwright/test'

const receiver = 'http://127.0.0.1:8767'

test('MQTT settings persist and publish real JSON to the selected topic', async ({ page }) => {
  test.skip(!process.env.MQTT_TEST_BROKER_URL, 'run scripts/run_full_tests.sh for the real MQTT broker')
  const topic = `speech/e2e/${Date.now()}`
  await page.goto('/destinations')
  await page.getByLabel('Name', { exact: true }).fill('MQTT receiver')
  await page.getByLabel('Transport').selectOption('mqtt')
  await page.getByLabel('Broker URL').fill(process.env.MQTT_TEST_BROKER_URL!)
  await page.getByLabel('Topic', { exact: true }).fill(topic)
  await page.getByLabel('QoS', { exact: true }).selectOption('2')
  await page.getByLabel('Retain the latest result on this topic').check()
  await expect(page.getByLabel('Custom headers (JSON)')).toHaveCount(0)
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('status')).toContainText('Saved “MQTT receiver”.')
  await page.reload()
  await page.getByRole('button', { name: /MQTT receiver/ }).click()
  await expect(page.getByLabel('Transport')).toHaveValue('mqtt')
  await expect(page.getByLabel('Topic', { exact: true })).toHaveValue(topic)
  await expect(page.getByLabel('QoS', { exact: true })).toHaveValue('2')
  await expect(page.getByLabel('Retain the latest result on this topic')).toBeChecked()
  await page.getByRole('button', { name: 'Send test JSON' }).click()
  await expect(page.getByRole('status')).toContainText('Published to MQTT broker')
  await expect.poll(async () => {
    const result = await page.request.get(`${receiver}/mqtt-records`).then(response => response.json())
    return result.records.find((record: { topic: string }) => record.topic === topic)
  }).toMatchObject({ topic, qos: 2, body: { event_type: 'transcription.match_results', transcript: 'Synthetic destination test' } })
  await page.screenshot({ path: '/tmp/echodesk-mqtt-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 360, height: 820 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  await page.screenshot({ path: '/tmp/echodesk-mqtt-mobile.png', fullPage: true })
})

test('MQTT form rejects wildcard topics and keeps HTTP settings separate', async ({ page }) => {
  await page.goto('/destinations')
  await page.getByLabel('Name', { exact: true }).fill('Invalid MQTT receiver')
  await page.getByLabel('Transport').selectOption('mqtt')
  await page.getByLabel('Topic', { exact: true }).fill('speech/#')
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('alert')).toContainText('publish topic')
  await page.getByLabel('Authentication', { exact: true }).selectOption('username_password')
  await page.getByLabel('Topic', { exact: true }).fill('speech/e2e/results')
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('alert')).toContainText('encrypted')
  await page.getByLabel('Transport').selectOption('http')
  await expect(page.getByLabel('Endpoint URL')).toBeVisible()
  await expect(page.getByLabel('Custom headers (JSON)')).toBeVisible()
  await expect(page.getByLabel('Topic', { exact: true })).toHaveCount(0)
})

test('loading MQTT settings does not clear retained data and save reports cleanup failure separately', async ({ page }) => {
  const destinationRequests: string[] = []
  page.on('request', request => {
    if (/\/api\/v1\/destinations(?:\/[^/]+)?$/.test(request.url()) && ['POST', 'PUT'].includes(request.method())) {
      destinationRequests.push(request.method())
    }
  })
  await page.goto('/destinations')
  expect(destinationRequests).toEqual([])
  await page.getByRole('button', { name: 'New destination' }).click()
  await page.getByLabel('Name', { exact: true }).fill('MQTT cleanup warning')
  await page.getByLabel('Transport').selectOption('mqtt')
  await page.getByLabel('Broker URL').fill('mqtt://127.0.0.1:1883')
  await page.getByLabel('Topic', { exact: true }).fill('speech/e2e/cleanup-warning')
  await page.route('**/api/v1/destinations', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 201, json: {
    destination_id: 'cleanup-destination', name: 'MQTT cleanup warning', endpoint_origin: 'mqtt://127.0.0.1:1883',
    method: 'POST', protocol: 'mqtt', mqtt_topic: 'speech/e2e/cleanup-warning', mqtt_qos: 1, mqtt_retain: false,
    auth_type: 'none', payload_format: 'match_results_v1',
    config: { endpoint_url: 'mqtt://127.0.0.1:1883', protocol: 'mqtt', mqtt_topic: 'speech/e2e/cleanup-warning',
      mqtt_qos: 1, mqtt_retain: false, headers: {}, auth: { type: 'none' }, method: 'POST', timeout_seconds: 5,
      max_attempts: 3, backoff_seconds: .25, max_backoff_seconds: 2, max_retry_after_seconds: 30,
      follow_redirects: false, idempotency_header: 'Idempotency-Key', payload_format: 'match_results_v1',
      tls_ca_bundle_ref: null, tls_client_certificate_ref: null, tls_client_key_ref: null },
    created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z',
    retained_cleanup: { status: 'failed', attempts: 3, error_code: 'delivery_failed' },
    } })
    : route.continue())
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('status')).toContainText('Saved “MQTT cleanup warning”.')
  await expect(page.getByRole('alert')).toContainText('Settings were saved, but clearing the old retained message failed')
  await expect(page.getByRole('alert')).toContainText('Save again to retry.')
  expect(destinationRequests).toEqual(['POST'])
})

test.afterEach(async ({ request }) => {
  const origin = { origin: 'http://127.0.0.1:4173' }
  const profiles = await request.get('/api/v1/profiles').then(response => response.json())
  for (const profile of profiles.profiles) await request.delete(`/api/v1/profiles/${profile.profile_id}`)
  const destinations = await request.get('/api/v1/destinations').then(response => response.json())
  for (const destination of destinations.destinations) await request.delete(`/api/v1/destinations/${destination.destination_id}`, { headers: origin })
  await request.post(`${receiver}/reset`)
})

test('creates, reopens, tests, updates, and protects a saved destination', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await page.request.post(`${receiver}/reset`)
  await page.goto('/destinations')
  await expect(page.getByRole('heading', { name: 'Destinations' })).toBeVisible()
  await page.getByRole('button', { name: 'New destination' }).click()
  await page.getByLabel('Name').fill('E2E receiver')
  await page.getByLabel('Endpoint URL').fill(`${receiver}/success`)
  await page.getByLabel('Custom headers (JSON)').fill('{"X-Station":"e2e"}')
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('status')).toContainText('Saved “E2E receiver”.')
  await expect(page.getByRole('button', { name: /E2E receiver/ })).toBeVisible()

  const testButton = page.getByRole('button', { name: 'Send test JSON' })
  await testButton.click()
  await expect(page.getByRole('status').filter({ hasText: 'Receiver accepted the test' })).toContainText('HTTP 204')
  const successRecord = await page.request.get(`${receiver}/records`).then(response => response.json())
  expect(successRecord.records.at(-1)).toMatchObject({
    path: '/success',
    headers: { 'x-station': 'e2e', 'content-type': 'application/json' },
    body: { event_type: 'transcription.match_results', transcript: 'Synthetic destination test', language: 'th', matches: [{ keyword: 'test', count: 1 }] },
  })

  await page.getByLabel('Name').fill('Unsaved receiver name')
  await expect(page.getByText('Save your changes before testing.')).toBeVisible()
  await expect(testButton).toBeDisabled()
  await page.getByLabel('Name').fill('E2E receiver')
  await page.reload()
  await page.getByRole('button', { name: /E2E receiver/ }).click()
  await expect(page.getByLabel('Endpoint URL')).toHaveValue(`${receiver}/success`)
  await expect(page.getByLabel('Custom headers (JSON)')).toHaveValue(`{
  "X-Station": "e2e"
}`)
  await expect(page.getByText('Redirects are disabled.')).toBeVisible()
  await expect(page.getByLabel('Follow redirects')).toHaveCount(0)
  await page.screenshot({ path: '/tmp/echodesk-destinations-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 360, height: 844 })
  await expect(page.getByRole('link', { name: 'Destinations' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  await page.screenshot({ path: '/tmp/echodesk-destinations-mobile.png', fullPage: true })
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.locator('#destination-timeout').fill('')
  await expect(page.locator('#destination-timeout')).toHaveValue('')
  expect(consoleErrors.some(message => message.includes('NaN'))).toBe(false)
  await page.locator('#destination-timeout').fill('5')
  await page.getByLabel('Custom headers (JSON)').fill('{"X-Station":"unsaved"}')
  await expect(page.getByText('Save your changes before testing.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send test JSON' })).toBeDisabled()
  await page.getByLabel('Custom headers (JSON)').fill(`{
  "X-Station": "e2e"
}`)

  await page.getByLabel('Endpoint URL').fill(`${receiver}/fail`)
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByRole('status')).toContainText('Saved “E2E receiver”.')
  await page.getByRole('button', { name: 'Send test JSON' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Test request failed' })).toContainText('3 attempts')

  const destinations = await page.request.get('/api/v1/destinations').then(response => response.json())
  const destination = destinations.destinations.find((item: { name: string }) => item.name === 'E2E receiver')
  const created = await page.request.post('/api/v1/profiles', { data: {
    name: 'E2E bound profile', device: 'E2E Studio Mic', execution_mode: 'shared', keywords: ['ทดสอบ'],
    silence_threshold: 0, model: 'small', runtime: 'openvino-gpu', destination_id: destination.destination_id,
  } })
  expect(created.ok()).toBeTruthy()
  await page.getByRole('button', { name: /E2E receiver/ }).click()
  await page.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByRole('alert')).toContainText('This destination is selected by a profile')
  await expect(page.getByRole('alert').getByRole('link', { name: 'Open profiles' })).toHaveAttribute('href', '/profiles')
  await expect(page.getByRole('button', { name: /E2E receiver/ })).toBeVisible()

  await page.request.delete(`/api/v1/profiles/${(await created.json()).profile_id}`)
  await page.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByRole('status')).toContainText('Deleted “E2E receiver”.')
})

test('profile destination survives save and duplicate, and run shows delivery independently', async ({ page }) => {
  const destinationResponse = await page.request.post('/api/v1/destinations', { data: {
    name: 'Profile output target', endpoint_url: `${receiver}/success`, method: 'POST', headers: {}, auth: { type: 'none' },
  }, headers: { origin: 'http://127.0.0.1:4173' } })
  expect(destinationResponse.ok()).toBeTruthy()
  const destination = await destinationResponse.json()
  await page.goto('/profiles/new')
  await page.getByLabel('Name').fill('Destination profile')
  await page.getByLabel('Microphone').selectOption('E2E Studio Mic')
  await page.getByLabel('One keyword per line').fill('สวัสดี')
  await page.getByLabel('Destination').selectOption(destination.destination_id)
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page).toHaveURL(/\/profiles$/)
  const saved = await page.request.get('/api/v1/profiles').then(async response => (await response.json()).profiles.find((profile: { name: string }) => profile.name === 'Destination profile'))
  expect(saved.destination_id).toBe(destination.destination_id)

  const profileRow = page.locator('.profile-record').filter({ hasText: 'Destination profile' })
  await profileRow.getByRole('link', { name: 'Duplicate' }).click()
  await expect(page.locator('#profile-destination')).toHaveValue(destination.destination_id)
  await page.getByLabel('Name').fill('Duplicated destination profile')
  await page.getByRole('button', { name: /Save profile/ }).click()
  const duplicate = await page.request.get('/api/v1/profiles').then(async response => (await response.json()).profiles.find((profile: { name: string }) => profile.name === 'Duplicated destination profile'))
  expect(duplicate.destination_id).toBe(destination.destination_id)

  await page.getByRole('button', { name: 'Start workflow Duplicated destination profile' }).click()
  await expect(page).toHaveURL(/\/runs\/[a-f0-9]+$/)
  await page.getByRole('button', { name: 'Stop listening' }).click()
  await expect(page.getByRole('region', { name: 'Output delivery status' })).toContainText('Destination delivery')
  await expect(page.getByRole('region', { name: 'Output delivery status' })).toContainText('failed')
})

test('destination form validates locally and reports API errors', async ({ page }) => {
  await page.goto('/destinations')
  await page.getByRole('button', { name: 'New destination' }).click()
  await page.getByLabel('Name').fill('Invalid URL receiver')
  await page.getByLabel('Endpoint URL').fill('javascript:alert(1)')
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('alert')).toContainText('valid absolute HTTP or HTTPS URL')

  await page.getByLabel('Endpoint URL').fill(`${receiver}/success`)
  await page.locator('#destination-retry-after').fill('0')
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('alert')).toContainText('Maximum Retry-After must be a finite number greater than 0.')
  await page.locator('#destination-retry-after').fill('30')
  await page.route('**/api/v1/destinations', async route => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 403, json: { detail: 'Destination administration is forbidden' } })
    await route.continue()
  })
  await page.getByRole('button', { name: 'Save destination' }).click()
  await expect(page.getByRole('alert')).toContainText('Destination administration is forbidden')
  await expect(page.getByText('OUTGOING BODY')).toBeVisible()

  await page.setViewportSize({ width: 360, height: 820 })
  await expect(page.getByRole('link', { name: 'Destinations' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
})

test('run detail reports delivery failure and reads attempts from the delivery receipt', async ({ page }) => {
  await page.route('**/api/v1/transcriptions/fixture-failed', route => route.fulfill({ json: {
    workflow_id: 'fixture-failed', kind: 'microphone', status: 'failed', keywords: [], profile_id: null,
    profile_name: null, device: null, execution_mode: null, silence_threshold: null, model: null, runtime: null,
    transcript: null, matches: null, latest_rtf: null, error: 'capture failed', destination_id: 'fixture-destination',
    delivery: { status: 'failed', destination_id: 'fixture-destination', attempts: 0, http_status: null, error_code: 'transcription_failed' },
    created_at: '2026-10-14T09:30:00Z',
  } }))
  await page.route('**/api/v1/transcriptions/fixture-failed/events?after=*', route => route.fulfill({ json: { events: [], next_cursor: 0 } }))
  await page.goto('/runs/fixture-failed')
  await expect(page.getByRole('region', { name: 'Output delivery status' })).toContainText('The workflow failed before a result could be sent.')

  await page.route('**/api/v1/transcriptions/fixture-delivered', route => route.fulfill({ json: {
    workflow_id: 'fixture-delivered', kind: 'microphone', status: 'completed', keywords: [], profile_id: null,
    profile_name: null, device: null, execution_mode: null, silence_threshold: null, model: null, runtime: null,
    transcript: 'สวัสดีครับ', matches: [], latest_rtf: null, error: null, destination_id: 'fixture-destination',
    delivery: { status: 'delivered', destination_id: 'fixture-destination', attempts: 2, http_status: 204, error_code: null },
    created_at: '2026-10-14T09:30:00Z',
  } }))
  await page.route('**/api/v1/transcriptions/fixture-delivered/events?after=*', route => route.fulfill({ json: { events: [
    { cursor: 1, type: 'forwarded', receipt: { attempts: 2, status_code: 204 }, timestamp: '2026-10-14T09:30:00Z' },
  ], next_cursor: 1 } }))
  await page.goto('/runs/fixture-delivered')
  await expect(page.getByText('Receiver accepted in 2 attempt(s).')).toBeVisible()
})
