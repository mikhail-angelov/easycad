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

  chat: (prompt: string, currentCode: string, provider: string, model?: string): Promise<StepResult> =>
    post('/api/chat', { prompt, current_code: currentCode, provider, model }),

  executeManual: (code: string): Promise<StepResult> => post('/api/execute-manual', { code }),

  revert: (stepId: number): Promise<SessionPayload> => post(`/api/steps/${stepId}/revert`, {}),

  exportUrl: (stepId: number): string => `/api/export/${stepId}`,
}
