import { test } from 'node:test'
import assert from 'node:assert/strict'
import { automationState, automationErrorCode, type AutomationInput } from './automation.ts'
import type { Step } from './api.ts'

function step(partial: Partial<Step> & { id: number }): Step {
  return {
    id: partial.id,
    kind: partial.kind ?? 'chat',
    original_prompt: null,
    refined_prompt: null,
    code: '',
    stl_base64: null,
    geometry_info: null,
    success: partial.success ?? true,
    error: partial.error ?? null,
    parent_id: null,
    created_at: 0,
  }
}

const base: AutomationInput = {
  busy: false,
  error: null,
  notice: null,
  pending: null,
  proposal: null,
  invalidNotice: null,
  variations: null,
  steps: [],
  currentId: null,
}

test('idle: fresh session with no steps', () => {
  assert.equal(automationState(base), 'idle')
})

test('idle: a reset session with only the initial step stays idle (not done)', () => {
  const steps = [step({ id: 1, kind: 'initial', success: true })]
  assert.equal(automationState({ ...base, steps, currentId: 1 }), 'idle')
})

test('generating: any busy wins, regardless of forks/error', () => {
  assert.equal(automationState({ ...base, busy: true }), 'generating')
  // busy beats an awaiting-input fork and an error.
  assert.equal(
    automationState({ ...base, busy: true, proposal: { originalPrompt: 'x', refinedPrompt: 'y' }, error: 'boom' }),
    'generating',
  )
})

test('awaiting-input: each of the four distinct forks', () => {
  assert.equal(automationState({ ...base, pending: { originalPrompt: 'p', questions: [] } }), 'awaiting-input')
  assert.equal(automationState({ ...base, proposal: { originalPrompt: 'p', refinedPrompt: 'r' } }), 'awaiting-input')
  assert.equal(automationState({ ...base, invalidNotice: { originalPrompt: 'p', reason: 'r' } }), 'awaiting-input')
  assert.equal(
    automationState({ ...base, variations: { candidates: [], originalPrompt: 'p', refined: null } }),
    'awaiting-input',
  )
})

test('confirm-refine (proposal) is awaiting-input, not done/idle', () => {
  // Regression: proposal must be its own fork, distinct from pending.
  const steps = [step({ id: 2, kind: 'chat', success: true })]
  const s = automationState({ ...base, proposal: { originalPrompt: 'p', refinedPrompt: 'r' }, steps, currentId: 2 })
  assert.equal(s, 'awaiting-input')
})

test('error: red error', () => {
  assert.equal(automationState({ ...base, error: 'boom' }), 'error')
})

test('error: a soft notice counts as error even over a prior good model', () => {
  const steps = [step({ id: 3, kind: 'chat', success: true })]
  const s = automationState({ ...base, notice: { message: 'busy', code: 'server_busy' }, steps, currentId: 3 })
  assert.equal(s, 'error')
})

test('done: a non-initial successful current step', () => {
  const steps = [step({ id: 1, kind: 'initial' }), step({ id: 2, kind: 'chat', success: true })]
  assert.equal(automationState({ ...base, steps, currentId: 2 }), 'done')
})

test('error state: a failed current step with error set reads error, not done', () => {
  const steps = [step({ id: 2, kind: 'chat', success: false, error: 'bad' })]
  assert.equal(automationState({ ...base, error: 'bad', steps, currentId: 2 }), 'error')
})

test('errorCode: notice.code exposed; error → "error"; absent otherwise', () => {
  assert.equal(automationErrorCode({ ...base, notice: { message: 'x', code: 'server_busy' } }), 'server_busy')
  assert.equal(automationErrorCode({ ...base, error: 'boom' }), 'error')
  assert.equal(automationErrorCode(base), undefined)
  // Not exposed while busy or awaiting-input.
  assert.equal(automationErrorCode({ ...base, busy: true, error: 'boom' }), undefined)
  assert.equal(automationErrorCode({ ...base, pending: { originalPrompt: 'p', questions: [] }, error: 'boom' }), undefined)
})
