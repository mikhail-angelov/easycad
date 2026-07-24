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

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const parsed = await res.json().catch(() => null)
    const detail = parsed?.detail
    // Coded errors ship `{detail: {code, message}}`; plain ones `{detail: "…"}`.
    if (detail && typeof detail === 'object') {
      throw new ApiError(detail.message ?? `Request failed: ${res.status}`, detail.code ?? null, res.status)
    }
    throw new ApiError(
      typeof detail === 'string' ? detail : `Request failed: ${res.status}`,
      null,
      res.status,
    )
  }
  return res.json()
}

function post<T>(url: string, body: unknown): Promise<T> {
  return send<T>('POST', url, body)
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
  ): Promise<ChatResponse> =>
    post('/api/chat', {
      prompt,
      current_code: currentCode,
      provider,
      model,
      auto_refine: autoRefine,
      refined_prompt: refinedPrompt,
    }),

  variations: (
    prompt: string,
    currentCode: string,
    provider: string,
    model: string | undefined,
    autoRefine: boolean,
    count = 3,
  ): Promise<VariationsResponse> =>
    post('/api/variations', {
      prompt,
      current_code: currentCode,
      provider,
      model,
      auto_refine: autoRefine,
      count,
    }),

  commit: (code: string, originalPrompt: string | null, refinedPrompt: string | null): Promise<StepResult> =>
    post('/api/commit', { code, original_prompt: originalPrompt, refined_prompt: refinedPrompt }),

  executeManual: (code: string): Promise<StepResult> => post('/api/execute-manual', { code }),

  revert: (stepId: number): Promise<SessionPayload> => post(`/api/steps/${stepId}/revert`, {}),

  exportUrl: (stepId: number): string => `/api/export/${stepId}`,

  exportProjectUrl: (): string => '/api/project/export',

  importProject: (project: unknown): Promise<SessionPayload> => post('/api/project/import', project),

  // ── Auth & settings (SPEC13) ──
  me: (): Promise<AuthInfo & { settings: SettingsInfo }> => fetch('/api/auth/me').then((r) => r.json()),

  login: (email: string): Promise<{ ok: boolean }> => post('/api/auth/login', { email }),

  logout: (): Promise<{ ok: boolean }> => post('/api/auth/logout', {}),

  deleteAccount: (): Promise<{ ok: boolean }> => send('DELETE', '/api/auth/me'),

  saveSettings: (patch: { provider?: string; model?: string; key?: string }): Promise<SettingsInfo> =>
    send('PUT', '/api/settings', patch),

  validateKey: (provider: string, key: string): Promise<ValidateKeyResult> =>
    post('/api/settings/validate-key', { provider, key }),
}
