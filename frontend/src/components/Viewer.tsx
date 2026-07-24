import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from '../api'
import { useStore, useT } from '../store'
import { ModelViewer } from '../viewer3d'

export function Viewer() {
  const stlBase64 = useStore((s) => s.stlBase64)
  const geometryInfo = useStore((s) => s.geometryInfo)
  const currentId = useStore((s) => s.currentId)

  const t = useT()
  const stageRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<ModelViewer | null>(null)
  const [wire, setWire] = useState(false)

  useEffect(() => {
    if (!stageRef.current) return
    const v = new ModelViewer(stageRef.current)
    viewerRef.current = v
    // Cover the race where the model arrived before the viewer mounted.
    const initial = useStore.getState().stlBase64
    if (initial) v.setSTL(initial)
    return () => {
      v.dispose()
      viewerRef.current = null
    }
  }, [])

  useEffect(() => {
    const v = viewerRef.current
    if (!v) return
    if (stlBase64) v.setSTL(stlBase64)
    else v.clear()
  }, [stlBase64])

  useEffect(() => {
    viewerRef.current?.setWireframe(wire)
  }, [wire])

  return (
    <section class="panel viewer-panel">
      <header>
        <h2>{t('viewer.title')}</h2>
        <div class="viewer-actions">
          <label class="wire-toggle">
            <input
              type="checkbox"
              checked={wire}
              onChange={(e) => setWire((e.target as HTMLInputElement).checked)}
            />
            {t('viewer.wireframe')}
          </label>
          {currentId != null && (
            <a class="text-button" href={api.exportUrl(currentId)} download>
              {t('viewer.exportStl')}
            </a>
          )}
        </div>
      </header>
      <div class="viewer-stage" ref={stageRef} />
      {geometryInfo && <pre class="geo-info">{geometryInfo}</pre>}
    </section>
  )
}
