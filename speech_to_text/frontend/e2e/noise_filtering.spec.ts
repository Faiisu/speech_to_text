import { expect, test } from '@playwright/test'

function toneWav(sampleRate: number) {
  const frames = sampleRate
  const buffer = Buffer.alloc(44 + frames * 2)
  buffer.write('RIFF', 0); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVE', 8)
  buffer.write('fmt ', 12); buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22); buffer.writeUInt32LE(sampleRate, 24); buffer.writeUInt32LE(sampleRate * 2, 28)
  buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34); buffer.write('data', 36); buffer.writeUInt32LE(frames * 2, 40)
  for (let frame = 0; frame < frames; frame++) buffer.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 1000 * frame / sampleRate)), 44 + frame * 2)
  return buffer
}

test('uploads, filters, and compares an actual saved WAV using the recording API', async ({ page }) => {
  await page.goto('/noise-filtering')
  await expect(page.getByRole('heading', { name: 'Noise filtering' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Noise filtering' })).toHaveClass(/active/)
  await page.getByLabel('Add an audio file').setInputFiles({ name: 'one-khz.wav', mimeType: 'audio/wav', buffer: toneWav(16000) })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(page.getByText('one-khz.wav is ready to filter.')).toBeVisible()
  await expect(page.getByLabel('Sample rate')).toContainText('16,000 Hz')
  await page.getByLabel('Low cutoff frequency').fill('300')
  await page.getByLabel('High cutoff frequency').fill('3000')
  await page.getByRole('button', { name: 'Filter and compare' }).click()
  await expect(page.getByRole('heading', { name: 'Before and after' })).toBeVisible()
  await expect(page.getByText('Filtered audio saved as a new recording. The source clip is unchanged.')).toBeVisible()
  await expect(page.locator('canvas[role="img"]')).toHaveCount(2)
  const sourceAudio = page.getByLabel('Before: one-khz.wav')
  await expect(sourceAudio).toHaveAttribute('src', /recordings\/[^/]+\/audio/)
  await expect(page.locator('audio[aria-label^="After:"]')).toHaveAttribute('src', /recordings\/[^/]+\/audio/)
  await expect.poll(() => sourceAudio.evaluate(element => (element as HTMLAudioElement).duration)).toBe(1)
  await expect(page.locator('.noise-filter-snapshot b')).toHaveText('one-khz-bandpass-300-3000.wav')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  await expect(page.locator('.noise-filter-plots canvas')).toHaveCount(2)
})

test('uses the uploaded WAV sample rate to constrain default cutoffs below Nyquist', async ({ page }) => {
  await page.goto('/noise-filtering')
  await page.getByLabel('Add an audio file').setInputFiles({ name: 'eight-khz.wav', mimeType: 'audio/wav', buffer: toneWav(8000) })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(page.getByLabel('Sample rate')).toContainText('8,000 Hz')
  await expect(page.getByLabel('High cutoff frequency')).toHaveValue('3500')
  await expect(page.getByRole('button', { name: 'Filter and compare' })).toBeEnabled()
})

test('keeps 24 kHz Y-axis labels and the 1 second X endpoint inside each canvas', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, '__canvasTextBounds', { value: [], writable: false })
    const originalFillText = CanvasRenderingContext2D.prototype.fillText
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
      const metrics = this.measureText(text)
      const width = Math.min(metrics.width, maxWidth ?? metrics.width)
      const left = this.textAlign === 'right' || this.textAlign === 'end' ? x - width : this.textAlign === 'center' ? x - width / 2 : x
      const height = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent
      const top = this.textBaseline === 'middle' ? y - height / 2 : this.textBaseline === 'top' || this.textBaseline === 'hanging' ? y : y - metrics.fontBoundingBoxAscent
      ;(window as Window & { __canvasTextBounds: Array<{ text: string; left: number; right: number; top: number; bottom: number; width: number; height: number }> }).__canvasTextBounds.push({
        text, left, right: left + width, top, bottom: top + height, width: this.canvas.width, height: this.canvas.height,
      })
      return originalFillText.call(this, text, x, y, maxWidth)
    }
  })
  await page.goto('/noise-filtering')
  await page.getByLabel('Add an audio file').setInputFiles({ name: 'wide-band.wav', mimeType: 'audio/wav', buffer: toneWav(48000) })
  await page.getByRole('button', { name: 'Upload audio' }).click()
  await expect(page.getByLabel('Sample rate')).toContainText('48,000 Hz')
  await page.getByRole('button', { name: 'Filter and compare' }).click()
  await expect(page.locator('.noise-filter-plots canvas')).toHaveCount(2)
  await expect.poll(() => page.evaluate(() => (window as Window & { __canvasTextBounds: Array<{ text: string }> }).__canvasTextBounds.length)).toBeGreaterThan(0)
  const labels = await page.evaluate(() => (window as Window & { __canvasTextBounds: Array<{ text: string; left: number; right: number; top: number; bottom: number; width: number; height: number }> }).__canvasTextBounds)
  for (const label of ['24,000', '18,000', '12,000', '6,000', '0']) expect(labels.some(item => item.text === label)).toBe(true)
  expect(labels.some(item => item.text === '1 s')).toBe(true)
  expect(labels.every(item => item.left >= 0 && item.right <= item.width && item.top >= 0 && item.bottom <= item.height)).toBe(true)
})
