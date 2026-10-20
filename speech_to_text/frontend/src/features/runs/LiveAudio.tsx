import { useEffect, useRef, useState } from 'react'

export default function LiveAudio({ workflowId, enabled }: { workflowId: string; enabled: boolean }) {
  const [listening, setListening] = useState(false)
  const [error, setError] = useState('')
  const connection = useRef<{ socket: WebSocket; context: AudioContext } | null>(null)
  const generation = useRef(0)

  function release() {
    generation.current += 1
    const current = connection.current
    connection.current = null
    if (current) {
      current.socket.close()
      void current.context.close()
    }
  }

  useEffect(() => {
    release()
    setListening(false)
    setError('')
    return release
  }, [workflowId, enabled])

  async function listen() {
    release()
    const token = generation.current
    setError('')
    let context: AudioContext | undefined
    try {
      context = new AudioContext()
      await context.resume()
      if (token !== generation.current) { void context.close(); return }
      const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/v1/transcriptions/${encodeURIComponent(workflowId)}/audio`)
      socket.binaryType = 'arraybuffer'
      const audioContext = context
      connection.current = { socket, context: audioContext }
      setListening(true)
      let ready = false
      let nextTime = 0
      const sources = new Set<AudioBufferSourceNode>()
      socket.onmessage = event => {
        if (connection.current?.socket !== socket) return
        if (typeof event.data === 'string') {
          try {
            const format = JSON.parse(event.data)
            if (format.sample_rate !== 16000 || format.channels !== 1 || format.format !== 'pcm_s16le') throw new Error()
            ready = true
          } catch { setError('Unsupported live audio format.'); release(); setListening(false) }
          return
        }
        if (!ready || !(event.data instanceof ArrayBuffer) || event.data.byteLength % 2) return
        const data = new DataView(event.data)
        const buffer = audioContext.createBuffer(1, data.byteLength / 2, 16000)
        const samples = buffer.getChannelData(0)
        for (let index = 0; index < samples.length; index++) samples[index] = data.getInt16(index * 2, true) / 32768
        // Drop queued playback after a slow connection or suspended browser tab.
        if (nextTime > audioContext.currentTime + 0.4) {
          for (const source of sources) source.stop()
          sources.clear()
          nextTime = audioContext.currentTime
        }
        const source = audioContext.createBufferSource()
        source.buffer = buffer
        source.connect(audioContext.destination)
        source.onended = () => { sources.delete(source); source.disconnect() }
        sources.add(source)
        nextTime = Math.max(nextTime, audioContext.currentTime + 0.03)
        source.start(nextTime)
        nextTime += buffer.duration
      }
      socket.onerror = () => {
        if (connection.current?.socket === socket) setError('Could not connect to live audio. Try again.')
      }
      socket.onclose = event => {
        if (connection.current?.socket !== socket) return
        if (event.code !== 1000) setError(event.reason || 'Live audio disconnected. Try again while the workflow is running.')
        release()
        setListening(false)
      }
    } catch {
      if (context && context.state !== 'closed') void context.close()
      if (token === generation.current) { setError('Could not start browser audio playback.'); setListening(false) }
    }
  }

  return <section className="panel live-audio-panel" aria-label="Live audio monitor">
    <div><h2>Live audio</h2><p>Listen on this device using the workflow’s microphone stream. Headphones help prevent feedback.</p></div>
    <button className="button button-outline" disabled={!enabled} onClick={() => {
      if (listening) { release(); setListening(false) } else void listen()
    }}>{listening ? 'Mute live audio' : 'Listen live'}</button>
    <small role="status">{listening ? 'Streaming to this device' : enabled ? 'Playback off · workflow continues independently' : 'Available while the workflow is recording'}</small>
    {error && <p role="alert">{error}</p>}
  </section>
}
