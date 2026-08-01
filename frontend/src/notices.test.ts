import { test } from 'node:test'
import assert from 'node:assert/strict'

import { LOCALIZED_CODES, SOFT_CODES, TRIAL_CODES, preservesPrompt } from './notices.ts'

// SPEC19 W1: operational failures are retryable "try again" notices; server_busy
// additionally preserves the submitted prompt so retry is one click.

test('operational (soft) codes are distinct from trial codes', () => {
  for (const code of SOFT_CODES) assert.equal(TRIAL_CODES.has(code), false, code)
})

test('every soft and trial code has localized copy registered', () => {
  for (const code of [...SOFT_CODES, ...TRIAL_CODES]) {
    assert.equal(LOCALIZED_CODES.has(code), true, `LOCALIZED_CODES missing ${code}`)
  }
})

test('only server_busy preserves the prompt for one-click retry', () => {
  assert.equal(preservesPrompt('server_busy'), true)
  for (const code of ['execution_timeout', 'worker_unavailable', 'trial_exhausted_anon', null, undefined]) {
    assert.equal(preservesPrompt(code as string | null | undefined), false, String(code))
  }
})
