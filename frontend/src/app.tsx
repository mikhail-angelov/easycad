import { useEffect } from 'preact/hooks'
import { useStore } from './store'
import { Editor } from './components/Editor'
import { Viewer } from './components/Viewer'
import { Chat } from './components/Chat'
import { Timeline } from './components/Timeline'

export function App() {
  const init = useStore((s) => s.init)
  const reset = useStore((s) => s.reset)
  const busy = useStore((s) => s.busy)

  useEffect(() => {
    init()
  }, [])

  return (
    <div class="app-shell">
      <header class="topbar">
        <div class="brand">
          <span class="brand-mark" />
          EasyCAD
          <span class="project-name">cadquery chat</span>
        </div>
        <button class="text-button" onClick={() => reset()} disabled={busy}>
          New model
        </button>
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
