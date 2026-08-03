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

test('SPEC22 automation contract selectors are present', () => {
  const app = source('./app.tsx')
  const chat = source('./components/Chat.tsx')
  const account = source('./components/Account.tsx')
  const viewer = source('./components/Viewer.tsx')
  const timeline = source('./components/Timeline.tsx')

  // App state root exposes the machine-readable signals.
  for (const attr of ['data-state', 'data-state-rev', 'data-error-code', 'data-auth-error', 'aria-busy']) {
    assert.match(app, new RegExp(attr))
  }
  // Auth-error banner.
  assert.match(app, /id="auth-error-banner"/)
  assert.match(app, /id="auth-error-dismiss"/)

  // Prompt input now has an id (not just a testid) and Send.
  assert.match(chat, /id="chat-prompt"/)
  assert.match(chat, /id="chat-send"/)

  // Awaiting-input forks: exact ids + disambiguated variation option cards.
  for (const id of ['proposal-use', 'proposal-cancel', 'invalid-generate', 'invalid-cancel', 'variation-commit', 'variation-cancel']) {
    assert.match(chat, new RegExp(`id="${id}"`))
  }
  assert.match(chat, /id=\{`variation-option-\$\{i\}`\}/)
  assert.match(chat, /id=\{`clarify-\$\{qi\}-\$\{oi\}`\}/)
  // The old ambiguous option id must be gone (would also match commit/cancel).
  assert.doesNotMatch(chat, /id=\{`variation-\$\{i\}`\}/)

  // Export selectors.
  for (const id of ['viewer-download', 'export-stl', 'export-step', 'export-source']) {
    assert.match(viewer, new RegExp(`id="${id}"`))
  }

  // Timeline per-step status.
  assert.match(timeline, /data-status=/)
  assert.match(timeline, /id=\{`timeline-step-\$\{s\.id\}`\}/)

  // Account token entry + revoke (no native confirm).
  for (const id of ['account-toggle', 'account-token-name', 'account-token-create', 'account-token-value', 'account-token-copy', 'account-token-error', 'account-delete-confirm', 'account-delete-cancel']) {
    assert.match(account, new RegExp(`id="${id}"`))
  }
  assert.match(account, /id=\{`token-revoke-\$\{tok\.id\}`\}/)
  // The native confirm() dialog that freezes agents must be gone.
  assert.doesNotMatch(account, /\bconfirm\(/)
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
