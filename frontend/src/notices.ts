// Single source of truth for how coded API errors map to UI notices (SPEC14/19).
// Shared by the store (notice vs red error, and prompt retention) and the Notice
// banner (localization + CTAs). Deliberately import-free so it is unit-testable
// headless (no preact/zustand/DOM), and so the two consumers can't drift.

// Trial-exhaustion codes: orange notice with "sign in / add key" CTAs.
export const TRIAL_CODES = new Set([
  'trial_exhausted_anon',
  'trial_exhausted_user',
  'trial_budget_exhausted',
])

// Operational failures (W1): worker under load / timeout / transport. Shown as an
// orange "try again" notice, never a red error — the request is retryable, not
// broken.
export const SOFT_CODES = new Set(['server_busy', 'execution_timeout', 'worker_unavailable'])

// Codes we ship localized copy for; anything else falls back to the server text.
export const LOCALIZED_CODES = new Set([...TRIAL_CODES, ...SOFT_CODES])

// Only `server_busy` preserves the submitted prompt so the user can retry in one
// click without retyping: the load spike is transient and prompt-independent,
// whereas a timeout/transport failure may warrant editing the prompt first.
export function preservesPrompt(code: string | null | undefined): boolean {
  return code === 'server_busy'
}
