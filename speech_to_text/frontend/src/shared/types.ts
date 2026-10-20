export type Microphone = {
  name: string
  selectable: boolean
  max_input_channels: number
  default_samplerate: number
  is_default: boolean
}

export type MicrophoneTest = {
  test_id: string
  status: string
  device: string | null
  sample_rate: number | null
  channels: number | null
  frames_received: number
  rms: number | null
  peak: number | null
  elapsed_seconds: number
  error: string | null
}

export type ModelChoice = {
  key: string
  display_name: string
  repository: string | null
  revision: string | null
  installed: boolean
  ready: boolean
  runtimes: RuntimeChoice[]
}

export type ModelDeletionPlan = {
  model_key: string
  files: { path: string; kind: 'source' | 'runtime'; bytes: number }[]
  total_bytes: number
  registered: boolean
  deletable: boolean
  reason: string | null
}

export type ModelDeletionResult = ModelDeletionPlan & { deleted: true }

export type ModelRuntime = {
  key: string
  available: boolean
  precision_options: string[]
  reason: string | null
}

export type ModelInspection = {
  repository: string
  revision: string
  compatible: boolean
  reasons: string[]
  model_type: string | null
  runtimes: ModelRuntime[]
}

export type ModelJobResult = {
  model_key: string
  repository: string
  revision: string
  downloaded: boolean
  converted: boolean
  verified: boolean
  runtime: string | null
  precision: string | null
  reason: string | null
}

export type ModelJob = {
  job_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  phase: string
  repository: string
  model_key: string
  revision: string
  operation: 'download' | 'convert'
  runtime: string | null
  precision: string | null
  created_at: string
  updated_at: string
  error: string | null
  result: ModelJobResult | null
}

export type RuntimeChoice = {
  key: 'openvino-gpu' | 'openvino-cpu' | 'ctranslate2'
  compatible: boolean
  ready: boolean
  precision_options: string[]
  reason: string | null
  default_precision?: string
  installed_precisions?: string[]
  has_generic_artifact?: boolean
}

export type Language = 'th' | 'en' | 'auto'
export const LANGUAGE_LABELS: Record<Language, string> = { th: 'Thai', en: 'English', auto: 'Auto detect' }

export type Profile = {
  profile_id: string
  name: string
  source_type?: 'microphone' | 'rtsp'
  device: string | null
  rtsp_url?: string | null
  rtsp_url_secret_ref?: string | null
  rtsp_username?: string | null
  rtsp_has_password?: boolean
  execution_mode: 'shared' | 'per_workflow_process'
  keywords: string[]
  silence_threshold: number
  model: string
  runtime: RuntimeChoice['key']
  language?: Language
  destination_id?: string | null
  decoding_options: DecodingOptions
  noise_filter?: NoiseFilterConfig
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
  source_type?: 'microphone' | 'rtsp'
  rtsp_url?: string | null
  rtsp_url_secret_ref?: string | null
  rtsp_username?: string | null
  rtsp_has_password?: boolean
  device: string | null
  execution_mode: 'shared' | 'per_workflow_process' | null
  silence_threshold: number | null
  decoding_options: DecodingOptions | null
  noise_filter?: NoiseFilterConfig | null
  model: string | null
  runtime: RuntimeChoice['key'] | null
  language?: Language | null
  transcript: string | null
  matches: { keyword: string; count: number }[] | null
  latest_rtf: number | null
  rtf_min?: number | null
  rtf_max?: number | null
  rtf_avg?: number | null
  rtf_count?: number
  error: string | null
  destination_id?: string | null
  delivery?: DeliveryStatus | null
  created_at: string
}

export type NoiseFilterConfig = {
  bandpass_enabled: boolean
  low_hz: number
  high_hz: number
  order: number
  denoise_enabled: boolean
  noise_recording_ids: string[]
  noise_sources?: { recording_id: string; filename: string }[]
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
  sample_rate?: number
  channels?: number
  device: string | null
  source?: 'microphone' | 'rtsp' | 'upload'
  rtsp_url?: string | null
  rtsp_url_secret_ref?: string | null
  rtsp_username?: string | null
  rtsp_has_password?: boolean
  filename?: string | null
  error: string | null
  created_at: string
  warnings?: string[]
}

export type RecordingSpectrogram = {
  times_seconds: number[]
  frequencies_hz: number[]
  dbfs: number[][]
}

export type NoiseProfileSummary = {
  recording_id?: string
  filename?: string
  recording_ids?: string[]
  sources?: { recording_id: string; filename: string }[]
  source_count?: number
  total_duration_seconds?: number
  combination?: string
  sample_rate: number
  duration_seconds: number
  fft_size: number
  overlap: number
  window: string
  frequency_bins: number
}

export type FilteredRecordingResult = {
  recording: Recording
  source: Recording
  source_recording_id: string
  settings: {
    low_hz: number
    high_hz: number
    order: number
    sample_rate: number
    channels: number
    duration_seconds: number
    nyquist_hz: number
    db_min: number
    db_max: number
    denoise_enabled: boolean
    noise_recording_id: string | null
    noise_filename: string | null
    noise_recording_ids: string[]
    noise_sources: { recording_id: string; filename: string }[]
    noise_source_count: number
    noise_total_duration_seconds: number | null
    noise_combination: string | null
    denoising_fft_size: number | null
    denoising_overlap: number | null
    denoising_window: string | null
    denoising_subtraction_strength: number | null
    denoising_gain_floor: number | null
    denoising_smoothing_size: number | null
    denoising_max_attenuation_db: number | null
  }
  before: RecordingSpectrogram
  after: RecordingSpectrogram
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
  recording_id?: string | null
  audio_filename?: string
  audio_duration_seconds?: number
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

export type ProfileDefinition = Pick<Profile, 'name' | 'device' | 'execution_mode' | 'keywords' | 'silence_threshold' | 'model' | 'runtime' | 'decoding_options' | 'noise_filter'> & { source_type?: 'microphone' | 'rtsp'; rtsp_url?: string | null; rtsp_url_secret_ref?: string | null; rtsp_username?: string | null; rtsp_password?: string; language: Language; destination_id?: string | null }
