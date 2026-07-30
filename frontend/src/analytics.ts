// Thin Yandex.Metrica event layer for the SPA.
//
// The counter *script* itself is injected server-side into the page <head>
// (only when YANDEX_METRICA_ID is configured — see app/main.py), which also
// sets `window.__ymId`. When the counter is unconfigured (dev, self-hosting)
// there is no `ym`/`__ymId`, so every call here is a silent no-op and call
// sites need no guards of their own.
//
// One taxonomy lives here so goal names don't drift across the codebase. Keep
// this list and the funnel it describes in sync with docs/todo.md.

declare global {
  interface Window {
    ym?: (id: number, action: string, ...args: unknown[]) => void
    __ymId?: number
  }
}

// The tracked funnel: landing_cta → app_open → prompt_sent → step_success (the
// "aha") → trial_exhausted. `generation_failed` / `clarify_verdict` /
// `invalid_verdict` are health signals, not funnel steps.
export type Goal =
  | 'app_open'
  | 'prompt_sent'
  | 'step_success'
  | 'generation_failed'
  | 'trial_exhausted'
  | 'clarify_verdict'
  | 'invalid_verdict'

export function track(goal: Goal, params?: Record<string, unknown>): void {
  const id = window.__ymId
  if (!id || typeof window.ym !== 'function') return
  // Metrica's reachGoal accepts an optional params object as the 4th arg.
  window.ym(id, 'reachGoal', goal, params)
}
