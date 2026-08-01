import { test } from 'node:test'
import assert from 'node:assert/strict'

import { translate } from './i18n.ts'

// SPEC19 W1: every coded notice the SPA localizes must have real EN *and* RU copy
// (a missing key would make translate() fall through to the raw key string).
const LOCALIZED = [
  'trial_exhausted_anon',
  'trial_exhausted_user',
  'trial_budget_exhausted',
  'server_busy',
  'execution_timeout',
  'worker_unavailable',
]

test('every localized notice code has distinct EN and RU copy', () => {
  for (const code of LOCALIZED) {
    const key = `notice.${code}`
    const en = translate('en', key)
    const ru = translate('ru', key)
    assert.notEqual(en, key, `EN copy missing for ${code}`)
    assert.notEqual(ru, key, `RU copy missing for ${code}`)
    assert.notEqual(en, ru, `RU copy not translated for ${code}`)
  }
})

test('an unknown code falls back to the key (Notice then shows server text instead)', () => {
  assert.equal(translate('en', 'notice.not_a_real_code'), 'notice.not_a_real_code')
})
