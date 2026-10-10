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

export type StressChunk = {
  source_id?: string
  sequence?: number
  queue_wait_seconds?: number | null
  inference_seconds?: number | null
  elapsed_seconds?: number | null
  audio_seconds?: number | null
  rtf?: number | null
  status?: string
  [key: string]: unknown
}

export type StressTrial = {
  status: 'completed' | 'unavailable' | string
  topology: 'shared-model' | 'per-input-model' | string
  workflow_count: number
  capacity_verdict?: 'pass' | 'fail' | 'unavailable' | string
  unavailable_reason?: string | null
  chunk_elapsed_seconds?: { p50?: number | null; p95?: number | null; maximum?: number | null }
  chunk_rtf?: { p50?: number | null; p95?: number | null; maximum?: number | null }
  inference_utilization_by_model?: Record<string, number>
  model_startup_seconds?: number | null
  peak_total_process_rss_bytes?: number | null
  peak_child_process_rss_bytes?: number | null
  max_observed_queue_depth?: number | null
  dropped_or_failed_chunks?: number
  failed_inference_chunks?: number
  chunks?: StressChunk[]
  [key: string]: unknown
}

export type StressReport = Record<string, { topology: string; trials: StressTrial[]; [key: string]: unknown }>

export type StressTest = {
  stress_test_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | string
  model: string
  runtime: RuntimeChoice['key']
  precision: string
  report: StressReport | null
  error: string | null
  created_at: number
}

export type StressEvent = {
  cursor: number
  type: string
  topology?: string
  workflow_count?: number
  completed_workflows?: number
  elapsed_seconds?: number
  capacity_verdict?: string
  reason?: string
  [key: string]: unknown
}

export type ProfileDefinition = Pick<Profile, 'name' | 'device' | 'execution_mode' | 'keywords' | 'silence_threshold' | 'model' | 'runtime'>
