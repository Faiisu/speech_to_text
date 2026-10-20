import type { Destination, DestinationDefinition, DestinationSummary, FilteredRecordingResult, Microphone, MicrophoneTest, ModelChoice, ModelDeletionPlan, ModelDeletionResult, ModelInspection, ModelJob, NoiseProfileSummary, Profile, ProfileDefinition, Recording, Transcription, WorkflowEvent } from './types'

const API_ROOT = '/api/v1'

export class ApiRequestError extends Error {
  constructor(message: string, readonly status: number, readonly detail: unknown = message) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new Error('Cannot reach the API. Make sure the backend is running at localhost:8000, then try again.')
  }
  if (!response.ok) {
    let detail: unknown = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body && 'detail' in body) detail = body.detail
    } catch { /* Keep the status message when the body is not JSON. */ }
    const message = typeof detail === 'string' ? detail : `Request failed (${response.status})`
    throw new ApiRequestError(message, response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  microphones: async () => (await request<{ devices: Microphone[] }>('/microphones')).devices,
  startMicrophoneTest: (device: string | null) => request<MicrophoneTest>('/microphones/test', { method: 'POST', body: JSON.stringify({ device }) }),
  microphoneTest: (id: string) => request<MicrophoneTest>(`/microphones/test/${encodeURIComponent(id)}`),
  stopMicrophoneTest: (id: string) => request<MicrophoneTest>(`/microphones/test/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  models: () => request<{ default_model: string; default_runtime: string; models: ModelChoice[] }>('/models'),
  inspectModel: (input: { repository: string; revision?: string; token?: string }) => request<ModelInspection>('/models/inspect', { method: 'POST', body: JSON.stringify(input) }),
  modelJobs: () => request<{ jobs: ModelJob[] }>('/models/jobs'),
  modelDeletionPlan: (key: string) => request<ModelDeletionPlan>(`/models/${encodeURIComponent(key)}/deletion`),
  deleteLocalModel: (key: string) => request<ModelDeletionResult>(`/models/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  startModelJob: (input: { repository: string; model_key: string; revision?: string; token?: string; operation: 'download' | 'convert'; runtime?: string; precision?: string }) => request<ModelJob>('/models/jobs', { method: 'POST', body: JSON.stringify(input) }),
  profiles: async () => (await request<{ profiles: Profile[] }>('/profiles')).profiles,
  profile: (id: string) => request<Profile>(`/profiles/${encodeURIComponent(id)}`),
  createProfile: (definition: ProfileDefinition) => request<Profile>('/profiles', { method: 'POST', body: JSON.stringify(definition) }),
  updateProfile: (id: string, definition: ProfileDefinition) => request<Profile>(`/profiles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(definition) }),
  deleteProfile: (id: string) => request<void>(`/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  destinations: async () => (await request<{ destinations: DestinationSummary[] }>('/destinations')).destinations,
  destination: (id: string) => request<Destination>(`/destinations/${encodeURIComponent(id)}`),
  createDestination: (definition: DestinationDefinition) => request<Destination>('/destinations', { method: 'POST', body: JSON.stringify(definition) }),
  updateDestination: (id: string, definition: DestinationDefinition) => request<Destination>(`/destinations/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(definition) }),
  deleteDestination: (id: string) => request<void>(`/destinations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testDestination: (id: string) => request<{ status: 'delivered' | 'failed'; event_id: string; attempts: number; http_status: number | null; error_code?: string; transport?: 'http' | 'mqtt'; mqtt_topic?: string | null; mqtt_qos?: number | null }>(`/destinations/${encodeURIComponent(id)}/test`, { method: 'POST' }),
  startProfile: (id: string) => request<{ workflow_id: string; status: string }>(`/profiles/${encodeURIComponent(id)}/runs`, { method: 'POST' }),
  transcriptions: async () => (await request<{ transcriptions: Transcription[] }>('/transcriptions')).transcriptions,
  transcription: (id: string) => request<Transcription>(`/transcriptions/${encodeURIComponent(id)}`),
  events: (id: string, after: number) => request<{ events: WorkflowEvent[]; next_cursor: number }>(`/transcriptions/${encodeURIComponent(id)}/events?after=${after}`),
  recordings: async () => (await request<{ recordings: Recording[] }>('/recordings')).recordings,
  startRecording: (device: string | null, source?: { source_type: 'microphone' | 'rtsp'; rtsp_url?: string; rtsp_url_secret_ref?: string; rtsp_username?: string; rtsp_password?: string }) => request<Recording>('/recordings', { method: 'POST', body: JSON.stringify({ device, ...source }) }),
  uploadRecording: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    const response = await fetch(`${API_ROOT}/recordings/upload`, { method: 'POST', body: form })
    if (!response.ok) {
      let detail: unknown = `Request failed (${response.status})`
      try { const body = await response.json(); if (body && 'detail' in body) detail = body.detail } catch { /* Keep the status message when the body is not JSON. */ }
      throw new ApiRequestError(typeof detail === 'string' ? detail : `Request failed (${response.status})`, response.status, detail)
    }
    return response.json() as Promise<Recording>
  },
  analyzeNoiseRecording: (recording_id: string) => request<NoiseProfileSummary>('/noise-filtering/noise-profiles', { method: 'POST', body: JSON.stringify({ recording_id }) }),
  analyzeNoiseRecordings: (recording_ids: string[]) => request<NoiseProfileSummary>('/noise-filtering/noise-profiles', { method: 'POST', body: JSON.stringify({ recording_ids }) }),
  filterRecording: (input: { recording_id: string; low_hz: number; high_hz: number; order?: number; denoise_enabled?: boolean; noise_recording_id?: string; noise_recording_ids?: string[] }) => request<FilteredRecordingResult>('/noise-filtering/recordings', { method: 'POST', body: JSON.stringify(input) }),
  recording: (id: string) => request<Recording>(`/recordings/${encodeURIComponent(id)}`),
  stopRecording: (id: string) => request<Recording>(`/recordings/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  deleteRecording: (id: string) => request<void>(`/recordings/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  recordingAudio: async (id: string) => {
    const response = await fetch(`${API_ROOT}/recordings/${encodeURIComponent(id)}/audio`)
    if (!response.ok) throw new ApiRequestError(response.status === 409 ? 'Recording is still in progress.' : `Could not load WAV audio (${response.status}).`, response.status)
    return response.blob()
  },
  transcribeClip: async (form: FormData) => {
    const response = await fetch(`${API_ROOT}/transcriptions/clips`, { method: 'POST', body: form })
    if (!response.ok) {
      let detail: unknown = `Request failed (${response.status})`
      try { const body = await response.json(); if (body && 'detail' in body) detail = body.detail } catch { /* Keep the status message when the body is not JSON. */ }
      throw new ApiRequestError(typeof detail === 'string' ? detail : `Request failed (${response.status})`, response.status, detail)
    }
    return response.json() as Promise<{ workflow_id: string; status: string }>
  },
  stop: (id: string) => request<{ workflow_id: string; status: string }>(`/transcriptions/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  startStressTest: (settings: { model?: string; runtime?: string; precision?: string }) => request<{ stress_test_id: string; status: string }>('/stress-tests', { method: 'POST', body: JSON.stringify(settings) }),
  stressTest: (id: string) => request<import('./types').StressTest>(`/stress-tests/${encodeURIComponent(id)}`),
  stressEvents: (id: string, after: number) => request<{ stress_test_id: string; events: import('./types').StressEvent[]; next_cursor: number }>(`/stress-tests/${encodeURIComponent(id)}/events?after=${after}`),
}
