import { useEffect, useRef } from 'preact/hooks'
import { api } from './api'
import { useStore } from './store'
import { Editor } from './components/Editor'
import { Viewer } from './components/Viewer'
import { Chat } from './components/Chat'
import { Timeline } from './components/Timeline'

export function App() {
  const init = useStore((s) => s.init)
  const reset = useStore((s) => s.reset)
  const importProject = useStore((s) => s.importProject)
  const busy = useStore((s) => s.busy)
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
          <span class="project-name">cadquery chat</span>
        </div>
        <div class="topbar-actions">
          <a class="text-button" href={api.exportProjectUrl()} download>
            Save project
          </a>
          <button class="text-button" onClick={() => fileRef.current?.click()} disabled={busy}>
            Load project
          </button>
          <button class="text-button" onClick={() => reset()} disabled={busy}>
            New model
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            style="display:none"
            onChange={onFile}
          />
        </div>
      </header>
      <div class="workspace">
        <Editor />
        <Viewer />
        <Chat />
      </div>
      <Timeline />
    </div>
  )
}
