// Typed client for the SPEC11 CadQuery Chat API.

export interface Step {
  id: number
  kind: 'initial' | 'chat' | 'manual'
  original_prompt: string | null
  refined_prompt: string | null
  code: string
  stl_base64: string | null // present on full-step responses, omitted in lists
  geometry_info: string | null
  success: boolean
  error: string | null
  parent_id: number | null
  created_at: number
}

export interface AuthInfo {
  authenticated: boolean
  email: string | null
}

export interface ProviderInfo {
  default_model: string
  models: string[]
  key_prefix: string
}

export type TrialTier = 'anon' | 'user' | 'byok'

export interface SettingsInfo {
  provider: string
  model: string | null
  has_key: boolean
  providers: Record<string, ProviderInfo>
  trial_tier?: TrialTier
  trial_remaining?: number | null
}

export interface SessionPayload {
  current_id: number | null
  current: Step | null
  steps: Step[]
  providers: Record<string, ProviderInfo>
  default_provider: string
  auth: AuthInfo
  settings: SettingsInfo
}

export interface ValidateKeyResult {
  ok: boolean
  reason: string | null
}

export interface StepResult {
  step: Step
  session: SessionPayload
}

export interface ClarifyQuestion {
  question: string
  options: string[]
}

export type ChatAction = 'generated' | 'confirm_refine' | 'clarify' | 'invalid'
export type ProgressStage = 'accepted' | 'refining' | 'generating' | 'executing' | 'repairing'

export interface ChatResponse {
  action: ChatAction
  questions: ClarifyQuestion[]
  original_prompt: string
  refined_prompt: string | null
  reason: string | null
  step: Step | null
  session: SessionPayload
}

export interface Candidate {
  code: string | null
  stl_base64: string | null
  geometry_info: string | null
  success: boolean
  error: string | null
}

export interface VariationsResponse {
  action: 'generated' | 'clarify' | 'invalid'
  questions: ClarifyQuestion[]
  reason: string | null
  original_prompt: string
  refined_prompt: string | null
  candidates: Candidate[]
  // Present on the 'generated' path: post-charge trial status, so the client
  // applies it instead of re-deriving the "charge once" rule (SPEC14).
  trial_tier?: TrialTier
  trial_remaining?: number | null
}

// Error carrying the API's stable machine-readable `code` (SPEC14) so the store
// can map code → orange notice vs. red error instead of matching on prose.
export class ApiError extends Error {
  code: string | null
  status: number
  constructor(message: string, code: string | null, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

export interface ReqOpts {
  timeoutMs?: number
}

export interface ChatStreamOptions {
  onProgress?: (stage: ProgressStage) => void
  signal?: AbortSignal
}

async function asApiError(res: Response): Promise<ApiError> {
  const parsed = await res.json().catch(() => null)
  const detail = parsed?.detail
  if (detail && typeof detail === 'object') {
    return new ApiError(detail.message ?? `Request failed: ${res.status}`, detail.code ?? null, res.status)
  }
  return new ApiError(
    typeof detail === 'string' ? detail : `Request failed: ${res.status}`,
    null,
    res.status,
  )
}

async function send<T>(method: string, url: string, body?: unknown, opts?: ReqOpts): Promise<T> {
  // Optional hard timeout (SPEC22): the boot exchange must never stall the SPA
  // render on a hung network. AbortSignal.timeout rejects the fetch after the
  // window, surfacing as a thrown error the caller treats like any failure.
  const signal = opts?.timeoutMs ? AbortSignal.timeout(opts.timeoutMs) : undefined
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    throw await asApiError(res)
  }
  return res.json()
}

function post<T>(url: string, body: unknown, opts?: ReqOpts): Promise<T> {
  return send<T>('POST', url, body, opts)
}

async function streamChat(body: unknown, opts?: ChatStreamOptions): Promise<ChatResponse> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal: opts?.signal,
  })
  if (!res.ok) throw await asApiError(res)
  if (!res.body) throw new Error('The server did not provide a chat stream.')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffered = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffered += decoder.decode(value, { stream: !done }).replaceAll('\r', '')
      let boundary = buffered.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = buffered.slice(0, boundary)
        buffered = buffered.slice(boundary + 2)
        boundary = buffered.indexOf('\n\n')
        const event = frame.match(/^event: (.+)$/m)?.[1]
        const raw = frame.match(/^data: (.+)$/m)?.[1]
        if (!event || !raw) continue
        const data = JSON.parse(raw)
        if (event === 'progress') {
          opts?.onProgress?.(data.stage as ProgressStage)
        } else if (event === 'result') {
          return data as ChatResponse
        } else if (event === 'error') {
          throw new ApiError(data.message ?? 'Chat request failed.', data.code ?? null, data.status ?? 500)
        }
      }
      if (done) break
    }
  } finally {
    reader.releaseLock()
  }
  throw new Error('The chat stream ended before returning a result.')
}

export interface TokenInfo {
  id: number
  name: string
  created_at: number
  expires_at: number
  revoked_at: number | null
}

export interface CreatedToken {
  id: number
  name: string
  token: string // the raw secret — shown once, never retrievable again
  created_at: number
}

export const api = {
  session: (): Promise<SessionPayload> => fetch('/api/session').then((r) => r.json()),

  reset: (): Promise<SessionPayload> => post('/api/session/reset', {}),

  chat: (
    prompt: string,
    currentCode: string,
    provider: string,
    model: string | undefined,
    autoRefine: boolean,
    refinedPrompt?: string,
    responseLanguage: 'en' | 'ru' = 'en',
    options?: ChatStreamOptions,
  ): Promise<ChatResponse> =>
    streamChat({
      prompt,
      current_code: currentCode,
      provider,
      model,
      auto_refine: autoRefine,
      refined_prompt: refinedPrompt,
      response_language: responseLanguage,
    }, options),

  variations: (
    prompt: string,
    currentCode: string,
    provider: string,
    model: string | undefined,
    autoRefine: boolean,
    count = 3,
    responseLanguage: 'en' | 'ru' = 'en',
  ): Promise<VariationsResponse> =>
    post('/api/variations', {
      prompt,
      current_code: currentCode,
      provider,
      model,
      auto_refine: autoRefine,
      count,
      response_language: responseLanguage,
    }),

  commit: (code: string, originalPrompt: string | null, refinedPrompt: string | null): Promise<StepResult> =>
    post('/api/commit', { code, original_prompt: originalPrompt, refined_prompt: refinedPrompt }),

  executeManual: (code: string): Promise<StepResult> => post('/api/execute-manual', { code }),

  revert: (stepId: number): Promise<SessionPayload> => post(`/api/steps/${stepId}/revert`, {}),

  exportUrl: (stepId: number): string => `/api/export/${stepId}`, // STL (cached)
  exportStepUrl: (stepId: number): string => `/api/export/${stepId}/step`,
  exportSourceUrl: (stepId: number): string => `/api/export/${stepId}/source`,

  exportProjectUrl: (): string => '/api/project/export',

  importProject: (project: unknown): Promise<SessionPayload> => post('/api/project/import', project),

  sendFeedback: (message: string, rating: number | null, email: string | null): Promise<{ ok: boolean }> =>
    post('/api/feedback', { message, rating, email }),

  // ── Auth & settings (SPEC13) ──
  me: (): Promise<AuthInfo & { settings: SettingsInfo }> => fetch('/api/auth/me').then((r) => r.json()),

  login: (email: string): Promise<{ ok: boolean }> => post('/api/auth/login', { email }),

  logout: (opts?: ReqOpts): Promise<{ ok: boolean }> => post('/api/auth/logout', {}, opts),

  // ── PAT bootstrap + management (SPEC22) ──
  // Exchange a Personal Access Token for the standard session cookie. Bounded by
  // a hard timeout so a hung request can't stall the SPA boot.
  authWithToken: (token: string, opts?: ReqOpts): Promise<{ ok: boolean; email: string }> =>
    post('/api/auth/token', { token }, opts),

  createToken: (name: string): Promise<CreatedToken> => post('/api/tokens', { name }),

  listTokens: (): Promise<TokenInfo[]> => send('GET', '/api/tokens'),

  revokeToken: (id: number): Promise<{ ok: boolean }> => send('DELETE', `/api/tokens/${id}`),

  deleteAccount: (): Promise<{ ok: boolean }> => send('DELETE', '/api/auth/me'),

  saveSettings: (patch: { provider?: string; model?: string; key?: string }): Promise<SettingsInfo> =>
    send('PUT', '/api/settings', patch),

  validateKey: (provider: string, key: string): Promise<ValidateKeyResult> =>
    post('/api/settings/validate-key', { provider, key }),
}
