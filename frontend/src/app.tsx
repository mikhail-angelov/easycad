import { useEffect, useRef, useState } from 'preact/hooks'
import { lazy, Suspense } from 'preact/compat'
import { api } from './api'
import { useStore, useT } from './store'
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

export function App() {
  const init = useStore((s) => s.init)
  const reset = useStore((s) => s.reset)
  const importProject = useStore((s) => s.importProject)
  const busy = useStore((s) => s.busy)
  const t = useT()
  const fileRef = useRef<HTMLInputElement>(null)
  const [codeVisible, setCodeVisible] = useState(savedCodeVisibility)

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
    <div class="app-shell">
      <header class="topbar">
        <div class="brand">
          <span class="brand-mark" />
          <span class="brand-title">text2part</span>
          <span class="project-name">{t('app.projectName')}</span>
        </div>
        <div class="topbar-actions">
          <button data-testid="code-toggle" class="topbar-action code-toggle" onClick={toggleCode} aria-pressed={codeVisible} title={codeVisible ? t('app.hideCode') : t('app.showCode')}>
            <span class="code-toggle-long">{codeVisible ? t('app.hideCode') : t('app.showCode')}</span>
            <span class="code-toggle-short">{t('app.code')}</span>
          </button>
          <LangToggle />
          <a data-testid="project-save" class="topbar-action" href={api.exportProjectUrl()} download title={t('app.saveProject')}>
            <IconSave />
            <span>{t('app.saveProject')}</span>
          </a>
          <button
            class="topbar-action"
            data-testid="project-load"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            title={t('app.loadProject')}
          >
            <IconLoad />
            <span>{t('app.loadProject')}</span>
          </button>
          <button data-testid="project-new" class="topbar-action" onClick={() => reset()} disabled={busy} title={t('app.newModel')}>
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
