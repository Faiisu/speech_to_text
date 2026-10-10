export type Microphone = {
  name: string
  selectable: boolean
  max_input_channels: number
  default_samplerate: number
  is_default: boolean
}

export type ModelChoice = {
  key: string
  display_name: string
  installed: boolean
  ready: boolean
  runtimes: RuntimeChoice[]
}

export type RuntimeChoice = {
  key: 'openvino-gpu' | 'openvino-cpu' | 'ctranslate2'
  compatible: boolean
  ready: boolean
  precision_options: string[]
  reason: string | null
}

export type Profile = {
  profile_id: string
  name: string
  device: string | null
  execution_mode: 'shared' | 'per_workflow_process'
  keywords: string[]
  silence_threshold: number
  model: string
  runtime: RuntimeChoice['key']
  created_at: string
  updated_at: string
}

export type Transcription = {
  workflow_id: string
  kind: 'clip' | 'microphone'
  status: string
  keywords: string[]
  profile_id: string | null
  profile_name: string | null
  device: string | null
  execution_mode: 'shared' | 'per_workflow_process' | null
  silence_threshold: number | null
  model: string | null
  runtime: RuntimeChoice['key'] | null
  transcript: string | null
  matches: { keyword: string; count: number }[] | null
  latest_rtf: number | null
  error: string | null
  created_at: string
}

export type WorkflowEvent = {
  cursor: number
  type: string
  [key: string]: unknown
}

export type ProfileDefinition = Pick<Profile, 'name' | 'device' | 'execution_mode' | 'keywords' | 'silence_threshold' | 'model' | 'runtime'>
