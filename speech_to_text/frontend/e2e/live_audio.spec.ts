import { expect, test } from '@playwright/test'

test('streams workflow PCM to the frontend and mutes without stopping capture', async ({ page }) => {
  await page.addInitScript(() => {
    const state = window as typeof window & { playedPackets: number }
    state.playedPackets = 0
    const original = AudioContext.prototype.createBufferSource
    AudioContext.prototype.createBufferSource = function () {
      state.playedPackets += 1
      return original.call(this)
    }
  })
  const response = await page.request.post('/api/v1/transcriptions/microphones', { data: { keywords: ['สวัสดี'] } })
  expect(response.ok()).toBeTruthy()
  const { workflow_id } = await response.json()
  await page.goto(`/runs/${workflow_id}`)
  const monitor = page.getByRole('region', { name: 'Live audio monitor' })
  const socketEvent = page.waitForEvent('websocket')
  await monitor.getByRole('button', { name: 'Listen live', exact: true }).click()
  const socket = await socketEvent
  let binaryBytes = 0
  socket.on('framereceived', frame => { if (Buffer.isBuffer(frame.payload)) binaryBytes += frame.payload.length })
  await expect(monitor).toContainText('Streaming to this device')
  await expect.poll(() => binaryBytes).toBeGreaterThan(0)
  await expect.poll(() => page.evaluate(() => (window as typeof window & { playedPackets: number }).playedPackets)).toBeGreaterThan(0)
  await page.screenshot({ path: '/tmp/live-audio-monitor.png', fullPage: true })
  await expect(monitor.getByRole('alert')).toHaveCount(0)
  await monitor.getByRole('button', { name: 'Mute live audio' }).click()
  await expect.poll(() => socket.isClosed()).toBe(true)
  expect((await page.request.get(`/api/v1/transcriptions/${workflow_id}`).then(r => r.json())).status).toBe('recording')
  await monitor.getByRole('button', { name: 'Listen live', exact: true }).click()
  await expect(monitor).toContainText('Streaming to this device')
  await page.getByRole('button', { name: 'Stop listening', exact: true }).click()
  await expect(monitor.getByRole('button', { name: 'Listen live', exact: true })).toBeDisabled()
})

test('leaving run detail closes its audio listener while workflow continues', async ({ page }) => {
  const response = await page.request.post('/api/v1/transcriptions/microphones', { data: { keywords: ['hello'], language: 'en' } })
  const { workflow_id } = await response.json()
  await page.goto(`/runs/${workflow_id}`)
  const socketEvent = page.waitForEvent('websocket')
  await page.getByRole('button', { name: 'Listen live', exact: true }).click()
  const socket = await socketEvent
  await expect(page.getByRole('button', { name: 'Mute live audio' })).toBeVisible()
  await page.getByRole('link', { name: '← Back to overview' }).click()
  await expect.poll(() => socket.isClosed()).toBe(true)
  expect((await page.request.get(`/api/v1/transcriptions/${workflow_id}`).then(r => r.json())).status).toBe('recording')
  await page.request.post(`/api/v1/transcriptions/${workflow_id}/stop`)
})
