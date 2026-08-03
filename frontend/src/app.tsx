import { useEffect, useRef, useState } from 'preact/hooks'
import { lazy, Suspense } from 'preact/compat'
import { api } from './api'
import { useStore, useT } from './store'
import { automationState, automationErrorCode } from './automation'
import { Chat } from './components/Chat'
import { Timeline } from './components/Timeline'
import { Account } from './components/Account'
import { Feedback } from './components/Feedback'
import { LangToggle } from './components/LangToggle'
import { IconSave, IconLoad, IconNew } from './components/Icons'

// Heavy panels (Monaco ~3 MB, three.js) are code-split into their own chunks so
// they don't bloat the initial bundle (review L1).
const Editor = lazy(() => import('./components/Editor').then((m) => ({ default: m.Editor })))
const Viewer = lazy(() => import('./components/Viewer').then((m) => ({ default: m.Viewer })))
const CODE_VISIBLE_KEY = 'easycad_code_visible'

function savedCodeVisibility() {
  try {
    return localStorage.getItem(CODE_VISIBLE_KEY) === '1'
  } catch {
    return false
  }
}

export function App({ authError }: { authError?: string | null } = {}) {
  const init = useStore((s) => s.init)
  const reset = useStore((s) => s.reset)
  const importProject = useStore((s) => s.importProject)
  const busy = useStore((s) => s.busy)
  const t = useT()
  const fileRef = useRef<HTMLInputElement>(null)
  const [codeVisible, setCodeVisible] = useState(savedCodeVisibility)

  // The full automation input (SPEC22 §4.1) — real store fields, derived into a
  // single `data-state` by one pure function so an agent drives us deterministically.
  const error = useStore((s) => s.error)
  const notice = useStore((s) => s.notice)
  const pending = useStore((s) => s.pending)
  const proposal = useStore((s) => s.proposal)
  const invalidNotice = useStore((s) => s.invalidNotice)
  const variations = useStore((s) => s.variations)
  const steps = useStore((s) => s.steps)
  const currentId = useStore((s) => s.currentId)
  const actionRev = useStore((s) => s.actionRev)

  const input = { busy, error, notice, pending, proposal, invalidNotice, variations, steps, currentId }
  const state = automationState(input)
  const errorCode = automationErrorCode(input)

  // A failed PAT bootstrap (§3.2) surfaces as a single neutral marker + a
  // dismissible banner — the SPA still renders on the free trial. Never echoes
  // the token or the cause.
  const [showAuthBanner, setShowAuthBanner] = useState(!!authError)

  useEffect(() => {
    init()
  }, [])

  const onFile = async (e: Event) => {
    const input = e.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = '' // allow re-selecting the same file later
    if (!file) return
    await importProject(await file.text())
  }

  const toggleCode = () => {
    setCodeVisible((visible) => {
      const next = !visible
      try {
        localStorage.setItem(CODE_VISIBLE_KEY, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  return (
    <div
      class="app-shell"
      data-state={state}
      data-state-rev={actionRev}
      {...(errorCode ? { 'data-error-code': errorCode } : {})}
      {...(authError ? { 'data-auth-error': authError } : {})}
      aria-busy={state === 'generating' ? 'true' : undefined}
    >
      {showAuthBanner && authError && (
        <div id="auth-error-banner" class="auth-error-banner" role="alert">
          <span>{t('auth.tokenError')}</span>
          <button id="auth-error-dismiss" class="text-link" onClick={() => setShowAuthBanner(false)}>
            ×
          </button>
        </div>
      )}
      <header class="topbar">
        <div class="brand">
          <span class="brand-mark" />
          <span class="brand-title">text2part</span>
          <span class="project-name">{t('app.projectName')}</span>
        </div>
        <div class="topbar-actions">
          <button id="code-toggle" data-testid="code-toggle" class="topbar-action code-toggle" onClick={toggleCode} aria-pressed={codeVisible} title={codeVisible ? t('app.hideCode') : t('app.showCode')}>
            <span class="code-toggle-long">{codeVisible ? t('app.hideCode') : t('app.showCode')}</span>
            <span class="code-toggle-short">{t('app.code')}</span>
          </button>
          <LangToggle />
          <a id="project-save" data-testid="project-save" class="topbar-action" href={api.exportProjectUrl()} download title={t('app.saveProject')}>
            <IconSave />
            <span>{t('app.saveProject')}</span>
          </a>
          <button
            class="topbar-action"
            id="project-load"
            data-testid="project-load"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            title={t('app.loadProject')}
          >
            <IconLoad />
            <span>{t('app.loadProject')}</span>
          </button>
          <button id="project-new" data-testid="project-new" class="topbar-action" onClick={() => reset()} disabled={busy} title={t('app.newModel')}>
            <IconNew />
            <span>{t('app.newModel')}</span>
          </button>
          <input
            ref={fileRef}
            name="project-file"
            data-testid="project-file"
            type="file"
            accept="application/json,.json"
            style="display:none"
            onChange={onFile}
          />
          <Feedback />
          <Account />
        </div>
      </header>
      <div class={`workspace ${codeVisible ? 'editor-open' : ''}`}>
        {codeVisible && (
          <Suspense fallback={<section class="panel">{t('app.loadingEditor')}</section>}>
            <Editor />
          </Suspense>
        )}
        <Suspense fallback={<section class="panel">{t('app.loadingViewer')}</section>}>
          <Viewer />
        </Suspense>
        <Chat />
      </div>
      <Timeline />
    </div>
  )
}
