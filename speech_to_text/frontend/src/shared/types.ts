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

export type Language = 'th' | 'en' | 'auto'
export const LANGUAGE_LABELS: Record<Language, string> = { th: 'Thai', en: 'English', auto: 'Auto detect' }

export type Profile = {
  profile_id: string
  name: string
  device: string | null
  execution_mode: 'shared' | 'per_workflow_process'
  keywords: string[]
  silence_threshold: number
  model: string
  runtime: RuntimeChoice['key']
  language?: Language
  destination_id?: string | null
  decoding_options: DecodingOptions
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
  decoding_options: DecodingOptions | null
  model: string | null
  runtime: RuntimeChoice['key'] | null
  language?: Language | null
  transcript: string | null
  matches: { keyword: string; count: number }[] | null
  latest_rtf: number | null
  error: string | null
  destination_id?: string | null
  delivery?: DeliveryStatus | null
  created_at: string
}

export type DecodingOptions = {
  beam_size: number
  temperature: number
  condition_on_previous_text: boolean
  no_repeat_ngram_size: number
  repetition_penalty: number
}

export type DeliveryStatus = {
  status: 'disabled' | 'pending' | 'delivered' | 'failed' | string
  destination_id?: string | null
  event_id?: string | null
  attempts?: number
  http_status?: number | null
  last_attempt_at?: string | null
  error_code?: string | null
  transport?: 'http' | 'mqtt' | null
  mqtt_topic?: string | null
  mqtt_qos?: number | null
}

export type DestinationSummary = {
  destination_id: string
  name: string
  endpoint_origin: string
  method: 'POST' | 'PUT' | 'PATCH'
  auth_type: 'none' | 'bearer' | 'basic' | 'api_key_header' | 'username_password'
  protocol?: 'http' | 'mqtt'
  mqtt_topic?: string | null
  mqtt_qos?: number | null
  mqtt_retain?: boolean | null
  payload_format: 'match_results_v1'
  created_at: string
  updated_at: string
}

export type DestinationDefinition = {
  name: string
  endpoint_url: string
  method: 'POST' | 'PUT' | 'PATCH'
  headers: Record<string, string>
  auth: {
    type: 'none' | 'bearer' | 'basic' | 'api_key_header' | 'username_password'
    secret_ref?: string | null
    username_secret_ref?: string | null
    password_secret_ref?: string | null
    header_name?: string | null
  }
  timeout_seconds: number
  max_attempts: number
  backoff_seconds: number
  max_backoff_seconds: number
  max_retry_after_seconds: number
  follow_redirects: boolean
  idempotency_header: string | null
  payload_format: 'match_results_v1'
  tls_ca_bundle_ref: string | null
  tls_client_certificate_ref: string | null
  tls_client_key_ref: string | null
  protocol?: 'http' | 'mqtt'
  mqtt_topic?: string | null
  mqtt_qos?: number
  mqtt_retain?: boolean
}

export type Destination = DestinationSummary & {
  config: Omit<DestinationDefinition, 'name'>
  retained_cleanup?: { status: 'cleared' | 'failed'; attempts: number; error_code: string | null }
}

export type WorkflowEvent = {
  cursor: number
  type: string
  [key: string]: unknown
}

export type Recording = {
  recording_id: string
  status: 'recording' | 'completed' | 'failed' | string
  duration_seconds: number
  device: string | null
  source?: string
  filename?: string | null
  error: string | null
  created_at: string
  warnings?: string[]
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

export type ProfileDefinition = Pick<Profile, 'name' | 'device' | 'execution_mode' | 'keywords' | 'silence_threshold' | 'model' | 'runtime' | 'decoding_options'> & { language: Language; destination_id?: string | null }
