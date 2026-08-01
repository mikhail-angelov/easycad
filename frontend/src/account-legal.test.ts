import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// SPEC19 W4 requires the legal pages to be linked from the app too (account
// panel), not only the landing footer. A source-level check keeps this covered
// without a DOM/render harness (glob is frontend/src/*.test.ts, so this lives in
// src/ and reaches into components/).
const src = readFileSync(
  fileURLToPath(new URL('./components/Account.tsx', import.meta.url)),
  'utf8',
)

test('account panel links /terms and /privacy', () => {
  assert.match(src, /href="\/terms"/)
  assert.match(src, /href="\/privacy"/)
})
