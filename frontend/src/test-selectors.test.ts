import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

function source(path: string) {
  return readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
}

test('primary workspace controls expose stable test selectors', () => {
  const app = source('./app.tsx')
  const chat = source('./components/Chat.tsx')
  const account = source('./components/Account.tsx')
  const viewer = source('./components/Viewer.tsx')

  for (const selector of ['code-toggle', 'project-save', 'project-load', 'project-new']) {
    assert.match(app, new RegExp(`data-testid="${selector}"`))
  }
  for (const selector of ['chat-prompt', 'chat-send', 'chat-variations', 'auto-refine', 'trial-cta']) {
    assert.match(chat, new RegExp(`data-testid="${selector}"`))
  }
  for (const selector of ['account-toggle', 'account-login-email', 'account-login-submit', 'account-api-key']) {
    assert.match(account, new RegExp(`data-testid="${selector}"`))
  }
  for (const selector of ['viewer-wireframe', 'viewer-download']) {
    assert.match(viewer, new RegExp(`data-testid="${selector}"`))
  }
})

test('form controls have stable names for browser automation', () => {
  const app = source('./app.tsx')
  const chat = source('./components/Chat.tsx')
  const account = source('./components/Account.tsx')
  const viewer = source('./components/Viewer.tsx')

  for (const [src, name] of [
    [app, 'project-file'], [chat, 'auto-refine'], [chat, 'model'], [chat, 'chat-prompt'],
    [account, 'login-email'], [account, 'provider'], [account, 'model'], [account, 'api-key'],
    [viewer, 'wireframe'],
  ]) {
    assert.match(src, new RegExp(`name="${name}"`))
  }
})

test('interactive model surfaces have stable ids and accessible names', () => {
  const editor = source('./components/Editor.tsx')
  const viewer = source('./viewer3d.ts')

  assert.match(editor, /id="code-editor"/)
  assert.match(editor, /aria-label=/)
  assert.match(viewer, /domElement\.id = 'model-canvas'/)
  assert.match(viewer, /setAttribute\('aria-label', '3D model viewer'\)/)
})

test('every directly interactive JSX element has an id or name', () => {
  const paths = [
    './app.tsx', './components/Account.tsx', './components/Chat.tsx',
    './components/Editor.tsx', './components/Feedback.tsx', './components/LangToggle.tsx',
    './components/Notice.tsx', './components/Timeline.tsx', './components/Viewer.tsx',
  ]
  const interactive = new Set(['a', 'button', 'input', 'select', 'summary', 'textarea'])
  const missing: string[] = []

  for (const path of paths) {
    const text = source(path)
    // JSX event handlers contain `=>`; remove that one non-attribute `>` before
    // scanning opening tags. This is intentionally small because it guards a
    // simple markup convention, not TypeScript syntax.
    const openings = text.replaceAll('=>', '=').matchAll(/<(a|button|input|select|summary|textarea)\b([^>]*)>/g)
    for (const match of openings) {
      const [, tag, attrs] = match
      if (interactive.has(tag) && !/\b(?:id|name)=/.test(attrs)) {
        missing.push(`${path} <${tag}>`)
      }
    }
  }

  assert.deepEqual(missing, [])
})
