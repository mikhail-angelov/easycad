// The DOM automation state machine (SPEC22 §4.1).
//
// One pure function maps the app's real store fields to a single machine-readable
// `data-state` an agent can poll. Keeping it pure and dependency-free is the point:
// it's what the unit tests exercise directly, and `app.tsx` renders its result on
// the root element alongside `data-state-rev` for race-free waiting.

import type { InvalidNotice, Notice, Pending, Proposal, Variations } from './store'
import type { Step } from './api'

export type AutomationState = 'idle' | 'generating' | 'awaiting-input' | 'done' | 'error'

// One of the four distinct chat forks is open (SPEC22 §4.1). Shared by the state
// machine and the error-code deriver so a new fork can't drift them apart.
function hasAwaitingInput(i: Pick<AutomationInput, 'pending' | 'proposal' | 'invalidNotice' | 'variations'>): boolean {
  return !!(i.pending || i.proposal || i.invalidNotice || i.variations)
}

export interface AutomationInput {
  busy: boolean // any in-flight op (gen, manual execute, reset/import/revert, variation commit)
  error: string | null // red/hard error
  notice: Notice | null // operational failure — server_busy / timeout / trial (has .code)
  pending: Pending | null // clarify questions fork
  proposal: Proposal | null // confirm-refine fork
  invalidNotice: InvalidNotice | null // invalid-prompt fork
  variations: Variations | null // variation-pick fork
  steps: Step[]
  currentId: number | null
}

// State priority — first match wins (SPEC22 §4.1):
//   1. busy                                            → generating
//   2. pending|proposal|invalidNotice|variations       → awaiting-input
//   3. error|notice                                    → error
//   4. a NON-INITIAL current step that succeeded       → done
//   5. otherwise (initial / after reset)               → idle
export function automationState(i: AutomationInput): AutomationState {
  // #1 — any busy, NOT busyKind==='gen': manual execute / reset / revert /
  // variation-commit set busy with busyKind=null, and the agent must not act
  // mid-operation on any of them.
  if (i.busy) return 'generating'
  // #2 — the four distinct chat forks the existing flow can produce.
  if (hasAwaitingInput(i)) return 'awaiting-input'
  // #3 — operational failures live in `notice` with error===null; still an error
  // state so a failed last turn on top of a prior good model doesn't read `done`.
  if (i.error || i.notice) return 'error'
  // #4 — done requires a non-initial successful current step. Init/reset create a
  // successful `initial` step, so excluding it keeps `idle` reachable (#5).
  const cur = i.currentId != null ? i.steps.find((s) => s.id === i.currentId) : undefined
  if (cur && cur.kind !== 'initial' && cur.success) return 'done'
  return 'idle'
}

// The machine-readable error code exposed as `data-error-code` so the agent can
// branch on `server_busy` (retryable) vs hard errors. Absent when not in error.
export function automationErrorCode(i: Pick<AutomationInput, 'busy' | 'error' | 'notice' | 'pending' | 'proposal' | 'invalidNotice' | 'variations'>): string | undefined {
  if (i.busy) return undefined
  if (hasAwaitingInput(i)) return undefined
  if (i.notice) return i.notice.code ?? 'error'
  if (i.error) return 'error'
  return undefined
}
