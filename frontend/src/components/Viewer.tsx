import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from '../api'
import { useStore, useT } from '../store'
import { ModelViewer } from '../viewer3d'
import { formatGeometryInfo } from '../geometry'
import { IconCode, IconCube, IconDownload, IconMesh } from './Icons'

export function Viewer() {
  const stlBase64 = useStore((s) => s.stlBase64)
  const geometryInfo = useStore((s) => s.geometryInfo)
  const faceMesh = useStore((s) => s.faceMesh)
  const selectedFace = useStore((s) => s.selectedFace)
  const setSelectedFace = useStore((s) => s.setSelectedFace)
  const currentId = useStore((s) => s.currentId)

  const t = useT()
  const stageRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<ModelViewer | null>(null)
  const [wire, setWire] = useState(false)
  const [picking, setPicking] = useState(false)
  const [dlOpen, setDlOpen] = useState(false)
  const dlRef = useRef<HTMLDivElement>(null)

  // Close the download menu on an outside click.
  useEffect(() => {
    if (!dlOpen) return
    const onDoc = (e: MouseEvent) => {
      if (dlRef.current && !dlRef.current.contains(e.target as Node)) setDlOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [dlOpen])

  useEffect(() => {
    if (!stageRef.current) return
    const v = new ModelViewer(stageRef.current)
    viewerRef.current = v
    // Cover the race where the model arrived before the viewer mounted.
    const initial = useStore.getState().stlBase64
    const initialMesh = useStore.getState().faceMesh
    if (initialMesh) v.setFaceMesh(initialMesh)
    else if (initial) v.setSTL(initial)
    return () => {
      v.dispose()
      viewerRef.current = null
    }
  }, [])

  useEffect(() => {
    const v = viewerRef.current
    if (!v) return
    if (faceMesh) v.setFaceMesh(faceMesh)
    else if (stlBase64) v.setSTL(stlBase64)
    else v.clear()
  }, [stlBase64, faceMesh])

  useEffect(() => {
    viewerRef.current?.setWireframe(wire)
  }, [wire])

  useEffect(() => {
    viewerRef.current?.selectFace(selectedFace?.faceId ?? null)
  }, [selectedFace])

  useEffect(() => {
    viewerRef.current?.setFacePicking(picking ? (faceId) => {
      const face = faceMesh?.faces.find((item) => item.id === faceId)
      if (!face?.planar || face.center == null || !faceMesh) return
      setSelectedFace({ revision: faceMesh.revision, faceId, label: face.label })
      setPicking(false)
    } : null)
  }, [picking, faceMesh, setSelectedFace])

  return (
    <section class="panel viewer-panel">
      <header>
        <h2>{t('viewer.title')}</h2>
        <div class="viewer-actions">
          <label class="wire-toggle">
            <input
              type="checkbox"
              name="wireframe"
              data-testid="viewer-wireframe"
              checked={wire}
              onChange={(e) => setWire((e.target as HTMLInputElement).checked)}
            />
            {t('viewer.wireframe')}
          </label>
          {faceMesh && (
            <button
              class={`text-button ${picking ? 'active' : ''}`}
              type="button"
              id="viewer-select-face"
              data-testid="viewer-select-face"
              aria-pressed={picking}
              onClick={() => setPicking((value) => !value)}
            >
              {picking ? t('viewer.clickSurface') : selectedFace
                ? t('viewer.selectedSurface', { label: selectedFace.label }) : t('viewer.selectSurface')}
            </button>
          )}
          {selectedFace && (
            <button id="viewer-clear-face" type="button" class="text-button" onClick={() => setSelectedFace(null)}>
              × {t('viewer.targetSurface', { label: selectedFace.label })}
            </button>
          )}
          {currentId != null && (
            <div class="export-menu" ref={dlRef}>
              <button id="viewer-download" data-testid="viewer-download" class="text-button" onClick={() => setDlOpen((v) => !v)}>
                <IconDownload /> {t('viewer.download')} <span class="caret">▾</span>
              </button>
              {dlOpen && (
                <div class="export-dropdown">
                  <a id="export-stl" class="export-item" href={api.exportUrl(currentId)} download onClick={() => setDlOpen(false)}>
                    <IconMesh />
                    <span class="export-fmt">STL</span>
                    <span class="export-hint">{t('viewer.hintMesh')}</span>
                  </a>
                  <a id="export-step" class="export-item" href={api.exportStepUrl(currentId)} download onClick={() => setDlOpen(false)}>
                    <IconCube />
                    <span class="export-fmt">STEP</span>
                    <span class="export-hint">{t('viewer.hintCad')}</span>
                  </a>
                  <a id="export-source" class="export-item" href={api.exportSourceUrl(currentId)} download onClick={() => setDlOpen(false)}>
                    <IconCode />
                    <span class="export-fmt">.py</span>
                    <span class="export-hint">{t('viewer.hintSource')}</span>
                  </a>
                </div>
              )}
            </div>
          )}
        </div>
      </header>
      <div class="viewer-stage" ref={stageRef} />
      {geometryInfo && <div class="geo-info">{formatGeometryInfo(geometryInfo, t)}</div>}
    </section>
  )
}
