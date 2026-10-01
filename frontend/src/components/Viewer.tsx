import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from '../api'
import { useStore, useT } from '../store'
import { ModelViewer } from '../viewer3d'
import { formatGeometryInfo } from '../geometry'
import type { FaceInfo, FaceMesh } from '../api'
import { IconCode, IconCube, IconDownload, IconMesh } from './Icons'

function formatOrientation(normal: [number, number, number]) {
  const axes = ['X', 'Y', 'Z']
  const index = normal.reduce((best, value, current) => Math.abs(value) > Math.abs(normal[best]) ? current : best, 0)
  if (Math.abs(normal[index]) > 0.999) return `${normal[index] >= 0 ? '+' : '-'}${axes[index]}`
  return `(${normal.map((value) => value.toFixed(2)).join(', ')})`
}

function faceSize(face: FaceInfo, mesh: FaceMesh) {
  if (face.size) return face.size
  if (!face.normal) return null
  const normalAxis = face.normal.reduce((best, value, current) =>
    Math.abs(value) > Math.abs(face.normal![best]) ? current : best, 0)
  const axes = [0, 1, 2].filter((axis) => axis !== normalAxis)
  const ranges = axes.map(() => [Infinity, -Infinity])
  for (let i = face.start; i < face.start + face.count; i++) {
    const vertex = mesh.indices[i] * 3
    axes.forEach((axis, range) => {
      const value = mesh.positions[vertex + axis]
      ranges[range][0] = Math.min(ranges[range][0], value)
      ranges[range][1] = Math.max(ranges[range][1], value)
    })
  }
  return ranges.map(([min, max]) => max - min) as [number, number]
}

export function Viewer() {
  const stlBase64 = useStore((s) => s.stlBase64)
  const geometryInfo = useStore((s) => s.geometryInfo)
  const faceMesh = useStore((s) => s.faceMesh)
  const selectedFace = useStore((s) => s.selectedFace)
  const setSelectedFace = useStore((s) => s.setSelectedFace)
  const currentId = useStore((s) => s.currentId)
  const selectedFaceInfo = selectedFace && faceMesh?.faces.find((face) => face.id === selectedFace.faceId)
  const selectedFaceSize = selectedFaceInfo && faceMesh ? faceSize(selectedFaceInfo, faceMesh) : null

  const t = useT()
  const stageRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<ModelViewer | null>(null)
  const [wire, setWire] = useState(false)
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
    viewerRef.current?.setFaceSelectionHandler(faceMesh ? (faceId) => {
      const face = faceMesh?.faces.find((item) => item.id === faceId)
      if (!face?.planar || face.center == null || !faceMesh) return
      if (selectedFace?.faceId === faceId) {
        setSelectedFace(null)
      } else {
        setSelectedFace({ revision: faceMesh.revision, faceId, label: face.label })
      }
    } : null)
  }, [faceMesh, selectedFace, setSelectedFace])

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
      {selectedFaceInfo?.normal && selectedFaceSize && (
        <div class="face-info" data-testid="viewer-face-info">
          <span>{t('viewer.faceOrientation')}: <strong>{formatOrientation(selectedFaceInfo.normal)}</strong></span>
          <span>{t('viewer.faceSize')}: <strong>{selectedFaceSize.map((value) => value.toFixed(1)).join(' × ')} {t('geometry.mm')}</strong></span>
        </div>
      )}
      {geometryInfo && <div class="geo-info">{formatGeometryInfo(geometryInfo, t)}</div>}
    </section>
  )
}
