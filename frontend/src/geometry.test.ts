import { test } from 'node:test'
import assert from 'node:assert/strict'

import { formatGeometryInfo, parseGeometryInfo } from './geometry.ts'
import { translate } from './i18n.ts'

const INFO = '# Size: 10.0 x 20.0 x 30.0 mm\n# Topology: 1 solid(s), 6 faces, 12 edges'

test('parses worker geometry into language-neutral facts', () => {
  assert.deepEqual(parseGeometryInfo(INFO), {
    size: ['10.0', '20.0', '30.0'], solids: '1', faces: '6', edges: '12',
  })
  assert.equal(parseGeometryInfo('# ── Geometry info: could not extract ──'), null)
})

test('renders geometry in the selected interface language', () => {
  const en = formatGeometryInfo(INFO, (key, params) => translate('en', key, params))
  const ru = formatGeometryInfo(INFO, (key, params) => translate('ru', key, params))
  assert.match(en, /Size: 10.0 × 20.0 × 30.0 mm/)
  assert.match(ru, /Размер: 10.0 × 20.0 × 30.0 мм/)
  assert.equal(formatGeometryInfo(null, (key) => translate('ru', key)), 'Геометрия недоступна')
})
