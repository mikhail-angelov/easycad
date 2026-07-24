import { useEffect, useRef } from 'preact/hooks'
import { lazy, Suspense } from 'preact/compat'
import { api } from './api'
import { useStore, useT } from './store'
import { Chat } from './components/Chat'
import { Timeline } from './components/Timeline'
import { Account } from './components/Account'
import { LangToggle } from './components/LangToggle'
import { IconSave, IconLoad, IconNew } from './components/Icons'

// Heavy panels (Monaco ~3 MB, three.js) are code-split into their own chunks so
// they don't bloat the initial bundle (review L1).
const Editor = lazy(() => import('./components/Editor').then((m) => ({ default: m.Editor })))
const Viewer = lazy(() => import('./components/Viewer').then((m) => ({ default: m.Viewer })))

export function App() {
  const init = useStore((s) => s.init)
  const reset = useStore((s) => s.reset)
  const importProject = useStore((s) => s.importProject)
  const busy = useStore((s) => s.busy)
  const t = useT()
  const fileRef = useRef<HTMLInputElement>(null)

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

  return (
    <div class="app-shell">
      <header class="topbar">
        <div class="brand">
          <span class="brand-mark" />
          EasyCAD
          <span class="project-name">{t('app.projectName')}</span>
        </div>
        <div class="topbar-actions">
          <LangToggle />
          <a class="icon-button" href={api.exportProjectUrl()} download title={t('app.saveProject')}>
            <IconSave />
          </a>
          <button
            class="icon-button"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            title={t('app.loadProject')}
          >
            <IconLoad />
          </button>
          <button class="icon-button" onClick={() => reset()} disabled={busy} title={t('app.newModel')}>
            <IconNew />
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            style="display:none"
            onChange={onFile}
          />
          <Account />
        </div>
      </header>
      <div class="workspace">
        <Suspense fallback={<section class="panel">{t('app.loadingEditor')}</section>}>
          <Editor />
        </Suspense>
        <Suspense fallback={<section class="panel">{t('app.loadingViewer')}</section>}>
          <Viewer />
        </Suspense>
        <Chat />
      </div>
      <Timeline />
    </div>
  )
}
