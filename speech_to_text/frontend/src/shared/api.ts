import type { Microphone, ModelChoice, Profile, ProfileDefinition, Transcription, WorkflowEvent } from './types'

const API_ROOT = '/api/v1'

export class ApiRequestError extends Error {
  constructor(message: string, readonly status: number) {
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
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch { /* Keep the status message when the body is not JSON. */ }
    throw new ApiRequestError(detail, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  microphones: async () => (await request<{ devices: Microphone[] }>('/microphones')).devices,
  models: () => request<{ default_model: string; default_runtime: string; models: ModelChoice[] }>('/models'),
  profiles: async () => (await request<{ profiles: Profile[] }>('/profiles')).profiles,
  profile: (id: string) => request<Profile>(`/profiles/${encodeURIComponent(id)}`),
  createProfile: (definition: ProfileDefinition) => request<Profile>('/profiles', { method: 'POST', body: JSON.stringify(definition) }),
  updateProfile: (id: string, definition: ProfileDefinition) => request<Profile>(`/profiles/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(definition) }),
  deleteProfile: (id: string) => request<void>(`/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  startProfile: (id: string) => request<{ workflow_id: string; status: string }>(`/profiles/${encodeURIComponent(id)}/runs`, { method: 'POST' }),
  transcriptions: async () => (await request<{ transcriptions: Transcription[] }>('/transcriptions')).transcriptions,
  transcription: (id: string) => request<Transcription>(`/transcriptions/${encodeURIComponent(id)}`),
  events: (id: string, after: number) => request<{ events: WorkflowEvent[]; next_cursor: number }>(`/transcriptions/${encodeURIComponent(id)}/events?after=${after}`),
  stop: (id: string) => request<{ workflow_id: string; status: string }>(`/transcriptions/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
}
