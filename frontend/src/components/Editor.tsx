import { useEffect, useRef } from 'preact/hooks'
// Slim Monaco: the editor core + Python highlighting only (no TS/JSON/CSS/HTML
// language services or other grammars), which keeps the bundle small.
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import { useStore, useT } from '../store'

// Monaco only needs its core editor worker here (no TS/JSON language services).
;(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker: () => new editorWorker(),
}

export function Editor() {
  const code = useStore((s) => s.code)
  const setCode = useStore((s) => s.setCode)
  const runManual = useStore((s) => s.runManual)
  const busy = useStore((s) => s.busy)
  const t = useT()

  const hostRef = useRef<HTMLDivElement>(null)
  const edRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)

  useEffect(() => {
    if (!hostRef.current) return
    const ed = monaco.editor.create(hostRef.current, {
      value: useStore.getState().code,
      language: 'python',
      theme: 'vs',
      minimap: { enabled: false },
      fontSize: 12.5,
      lineNumbers: 'on',
      scrollBeyondLastLine: false,
      automaticLayout: true,
      tabSize: 4,
      renderWhitespace: 'none',
      padding: { top: 10 },
    })
    edRef.current = ed
    const sub = ed.onDidChangeModelContent(() => setCode(ed.getValue()))
    return () => {
      sub.dispose()
      ed.dispose()
      edRef.current = null
    }
  }, [])

  // Push external code changes (chat / revert / reset) into the editor without
  // clobbering the user's cursor when they are the source of the change.
  useEffect(() => {
    const ed = edRef.current
    if (ed && ed.getValue() !== code) ed.setValue(code)
  }, [code])

  return (
    <section class="panel editor-panel">
      <header>
        <h2>{t('editor.title')}</h2>
        <button class="text-button" disabled={busy} onClick={() => runManual()}>
          {t('editor.run')}
        </button>
      </header>
      <div class="code-host" ref={hostRef} />
    </section>
  )
}
