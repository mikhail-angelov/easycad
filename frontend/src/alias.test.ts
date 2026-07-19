import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { nextAlias } from './alias.ts'

describe('nextAlias', () => {
  it('produces single letters A-Z for the first 26 indices', () => {
    assert.equal(nextAlias(0), 'A')
    assert.equal(nextAlias(1), 'B')
    assert.equal(nextAlias(25), 'Z')
  })

  it('rolls over to AA, AB, ... after Z, matching spreadsheet column naming', () => {
    assert.equal(nextAlias(26), 'AA')
    assert.equal(nextAlias(27), 'AB')
    assert.equal(nextAlias(51), 'AZ')
    assert.equal(nextAlias(52), 'BA')
  })

  it('never repeats an alias across the first few hundred indices', () => {
    const seen = new Set<string>()
    for (let i = 0; i < 500; i += 1) {
      const alias = nextAlias(i)
      assert.equal(seen.has(alias), false, `duplicate alias ${alias} at index ${i}`)
      seen.add(alias)
    }
  })
})
