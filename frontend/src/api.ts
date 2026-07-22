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

export interface SessionPayload {
  current_id: number | null
  current: Step | null
  steps: Step[]
  providers: Record<string, string>
  default_provider: string
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
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail.detail ?? `Request failed: ${res.status}`)
  }
  return res.json()
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
}
