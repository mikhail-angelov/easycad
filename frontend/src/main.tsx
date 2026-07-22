import { render, type TargetedEvent } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { useAppStore, type AppState } from './store'
import type { ApiError, FeatureRosterEntry, ModelResponse } from './types'
import './styles.css'
import './model-viewer.css'

function errorFrom(payload: unknown): ApiError {
  const detail = (payload as { detail?: { message?: string; stage?: string } | Array<{ loc?: string[]; msg?: string }> })?.detail
  if (Array.isArray(detail)) {
    return { message: detail.map((item) => `${item.loc?.at(-1) || 'input'}: ${item.msg || 'invalid value'}`).join('; ') }
  }
  return { message: detail?.message || 'Unable to update the model.', stage: detail?.stage }
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw errorFrom(payload)
  return payload as T
}

async function uploadImage(state: AppState, file: File): Promise<void> {
  state.setRequestState('working')
  try {
    const form = new FormData()
    form.append('file', file)
    state.setModelResponse(await requestJson<ModelResponse>('/api/model/image', { method: 'POST', body: form }))
  } catch (error) { state.setError(error as ApiError) }
  finally { state.setRequestState('idle') }
}

async function generateFromText(state: AppState, instructions: string): Promise<void> {
  state.setRequestState('working')
  try {
    state.setModelResponse(await requestJson<ModelResponse>('/api/model/text', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instructions }),
    }))
  } catch (error) { state.setError(error as ApiError) }
  finally { state.setRequestState('idle') }
}

function UploadScreen() {
  const input = useRef<HTMLInputElement>(null)
  const state = useAppStore()
  const [description, setDescription] = useState('')
  const working = state.requestState === 'working'
  const change = (event: TargetedEvent<HTMLInputElement, Event>) => {
    const file = event.currentTarget.files?.[0]
    if (file) void uploadImage(state, file)
  }
  return <main class="upload-page"><div class="upload-card">
    <div class="brand"><span class="brand-mark" aria-hidden="true" /> EasyCAD</div>
    <p class="eyebrow">Drawing to 3D model</p>
    <h1>Upload a drawing.</h1>
    <p class="intro">EasyCAD returns a minimal reliable model immediately. You can then describe changes in plain text.</p>
    <input ref={input} class="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={change} />
    <button class="upload-target" type="button" disabled={working} onClick={() => input.current?.click()}>
      {working ? <><span class="spinner" /><span><strong>Building your model…</strong><small>Reading the drawing and rendering 3D geometry.</small></span></> : <><strong>Choose a drawing</strong><span>PNG, JPEG, or WebP</span></>}
    </button>
    <div class="or-divider"><span>or describe it in text</span></div>
    <label class="question-clarification">No drawing? Describe the part
      <textarea
        value={description}
        disabled={working}
        placeholder={'For example: "A box 100 x 50 x 30 mm with rounded edges and 2 mm wall thickness."'}
        onInput={(event: TargetedEvent<HTMLTextAreaElement, Event>) => setDescription(event.currentTarget.value)}
      />
    </label>
    <button class="text-generate" type="button" disabled={working || !description.trim()} onClick={() => void generateFromText(state, description)}>
      Generate from text
    </button>
    {state.error && <p class="field-error">{state.error.message}</p>}
    <div class="how-it-works">
      <div><span class="how-step">1</span>Upload a sketch, or describe the part in text</div>
      <div><span class="how-step">2</span>AI reads (or reasons out) the dimensions and features</div>
      <div><span class="how-step">3</span>Get an editable, printable 3D model</div>
    </div>
  </div></main>
}

function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }: {
  title: string; message: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void
}) {
  return <div class="modal-overlay" role="presentation" onClick={onCancel}>
    <div class="modal-card" role="dialog" aria-modal="true" aria-label={title} onClick={(event: Event) => event.stopPropagation()}>
      <h2>{title}</h2>
      <p>{message}</p>
      <div class="modal-actions">
        <button type="button" onClick={onCancel}>Cancel</button>
        <button type="button" class="danger" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </div>
  </div>
}

function FallbackBanner() {
  const state = useAppStore()
  const total = state.features.length
  const confirmed = state.features.filter((feature) => feature.status === 'confirmed').length
  if (total === 0 || confirmed > total / 2) return null
  return <div class="fallback-banner" role="status">
    <strong>Only {confirmed} of {total} detected features could be built reliably.</strong>
    <span>Try a clearer photo, or describe the missing parts below.</span>
  </div>
}

function formatExtent(extent: FeatureRosterEntry['extent']): string | null {
  if (!extent) return null
  const round = (value: number) => Math.round(value * 10) / 10
  const [minX, minY, minZ] = extent.minimum
  const [maxX, maxY, maxZ] = extent.maximum
  return `${round(maxX - minX)} × ${round(maxY - minY)} × ${round(maxZ - minZ)} mm`
}

function FeatureRoster() {
  const state = useAppStore()
  if (!state.features.length) return null
  const confirmed = state.features.filter((feature) => feature.status === 'confirmed').length
  // Omitted features sink to the bottom so the confirmed, buildable ones are always the
  // first thing scrolled into view; Array.prototype.sort is stable, so relative order within
  // each group (server/planner order) is preserved.
  const ordered = [...state.features].sort((a, b) => (a.status === 'unsupported' ? 1 : 0) - (b.status === 'unsupported' ? 1 : 0))
  return <section class="review-section feature-roster" aria-label="Features in this model">
    <header><h3>Features</h3><span>{confirmed} of {state.features.length}</span></header>
    <div class="review-rows">
      {ordered.map((feature) => (
        <article
          class={`review-row ${state.selectedId === feature.id ? 'selected' : ''}`}
          key={feature.id}
          data-feature-id={feature.id}
          onMouseEnter={() => state.setSelectedId(feature.id)}
          onClick={() => state.setSelectedId(feature.id)}
        >
          <span class={`number ${feature.status === 'unsupported' ? 'status-unsupported' : 'status-confirmed'}`}>{state.featureAliases[feature.id]}</span>
          <div class="review-copy">
            <div class="review-title"><strong>{feature.label}</strong>{formatExtent(feature.extent) && <span class="dims">{formatExtent(feature.extent)}</span>}</div>
            {feature.status === 'unsupported' && <span>Omitted — {feature.omission_reason}</span>}
          </div>
        </article>
      ))}
    </div>
  </section>
}

function Workspace() {
  const state = useAppStore()
  const [prompt, setPrompt] = useState('')
  const [mention, setMention] = useState<{ start: number; text: string } | null>(null)
  const [confirmUploadOpen, setConfirmUploadOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const dropdownRef = useRef<HTMLUListElement>(null)
  const newFileInput = useRef<HTMLInputElement>(null)
  const pending = state.requestState === 'working'

  const handleNewFile = (event: TargetedEvent<HTMLInputElement, Event>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (file) void uploadImage(state, file)
  }

  useEffect(() => {
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node
      if (dropdownRef.current?.contains(target) || textareaRef.current?.contains(target)) return
      setMention(null)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => document.removeEventListener('mousedown', closeOnOutsideClick)
  }, [])

  const mentionMatches = mention
    ? state.features.filter((feature) => {
        const alias = (state.featureAliases[feature.id] || '').toLowerCase()
        const needle = mention.text.toLowerCase()
        return alias.startsWith(needle) || feature.label.toLowerCase().includes(needle)
      })
    : []

  const handlePromptInput = (event: TargetedEvent<HTMLTextAreaElement, Event>) => {
    const value = event.currentTarget.value
    setPrompt(value)
    const caret = event.currentTarget.selectionStart ?? value.length
    const openMention = value.slice(0, caret).match(/@(\w*)$/)
    setMention(openMention ? { start: caret - openMention[0].length, text: openMention[1] } : null)
  }

  const pendingCaret = useRef<number | null>(null)
  const insertMention = (featureId: string) => {
    const alias = state.featureAliases[featureId]
    if (!mention || !alias) return
    const before = prompt.slice(0, mention.start)
    const after = prompt.slice(mention.start + mention.text.length + 1)
    const token = `@${alias} `
    setPrompt(before + token + after)
    setMention(null)
    pendingCaret.current = before.length + token.length
  }
  // Runs synchronously right after Preact commits the new `value` to the DOM (before the
  // browser can process the next input event) — a requestAnimationFrame callback here would
  // race fast-following keystrokes and could reset the caret mid-word.
  useLayoutEffect(() => {
    if (pendingCaret.current == null) return
    const caret = pendingCaret.current
    pendingCaret.current = null
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.focus()
    textarea.setSelectionRange(caret, caret)
  })

  // Resolves @<alias> mentions against the CURRENT roster only — featureAliases is sticky
  // and never shrinks (Part B), so an alias typed by hand for a feature from an earlier,
  // superseded round must not resolve here even though the letter is still "known". The
  // matched token is also replaced with the feature's real label before the text is sent:
  // the server/LLM never has to interpret `@<alias>` syntax itself, only natural language
  // plus the separately-carried referenced_feature_ids.
  const resolveMentions = (text: string): { referencedFeatureIds: string[]; resolvedText: string } => {
    const liveIds = new Set(state.features.map((feature) => feature.id))
    const aliasToId = new Map(Object.entries(state.featureAliases).map(([id, alias]) => [alias, id]))
    const labelById = new Map(state.features.map((feature) => [feature.id, feature.label]))
    const ids = new Set<string>()
    const resolvedText = text.replace(/@(\w+)/g, (full, token: string) => {
      const id = aliasToId.get(token)
      if (!id || !liveIds.has(id)) return full
      ids.add(id)
      return labelById.get(id) || full
    })
    return { referencedFeatureIds: Array.from(ids), resolvedText }
  }

  const refine = async () => {
    if (!state.specification || !prompt.trim()) return
    state.setRequestState('working')
    try {
      const { referencedFeatureIds, resolvedText } = resolveMentions(prompt)
      state.setModelResponse(await requestJson<ModelResponse>('/api/model/refine', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specification: state.specification, prompt: resolvedText, referenced_feature_ids: referencedFeatureIds }),
      }))
      setPrompt('')
      setMention(null)
    } catch (error) { state.setError(error as ApiError) }
    finally { state.setRequestState('idle') }
  }
  const download = async () => {
    if (!state.specification) return
    try {
      const response = await fetch('/api/model/stl', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ specification: state.specification }) })
      if (!response.ok) throw errorFrom(await response.json().catch(() => ({})))
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = `${state.model?.id || 'easycad'}.stl`; link.click(); URL.revokeObjectURL(url)
    } catch (error) { state.setError(error as ApiError) }
  }
  return <main class="app-shell">
    <header class="topbar">
      <div class="brand"><span class="brand-mark" /> EasyCAD <span class="project-name">{state.specification?.title}</span></div>
      <input ref={newFileInput} class="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={handleNewFile} />
      <button class="text-button" type="button" onClick={() => setConfirmUploadOpen(true)}>Upload new drawing</button>
    </header>
    <section class="headline"><p class="eyebrow">Current 3D model</p><h1>{state.description}</h1><p>The model stays visible while you refine it.</p></section>
    <div class="workspace"><section class="drawing-panel"><ModelViewer stl={state.modelStl!} features={state.features} selectedId={state.selectedId} onSelect={state.setSelectedId} /></section>
      <section class="review-panel"><div class="review-heading"><div><p class="eyebrow">Change the model</p><h2>Describe the next change</h2></div></div>
        <FallbackBanner />
        <FeatureRoster />
        <div class="mention-host">
          <label class="question-clarification">Freeform instruction
            <textarea ref={textareaRef} value={prompt} placeholder={'For example: “remove the top cylinder” or “@B the top-hole diameter is 15 mm.” — type @ to reference a feature by name'} onInput={handlePromptInput} />
          </label>
          {mention && mentionMatches.length > 0 && <ul class="mention-dropdown" ref={dropdownRef}>
            {mentionMatches.map((feature) => (
              <li key={feature.id}><button type="button" onClick={() => insertMention(feature.id)}>
                <strong>{state.featureAliases[feature.id]}</strong> {feature.label}
              </button></li>
            ))}
          </ul>}
        </div>
        <button class="primary" type="button" disabled={pending || !prompt.trim()} onClick={() => void refine()}>{pending ? 'Updating model…' : 'Apply change'}</button>
        <div class="divider" />
        <p class="section-note">The displayed model is the exact STL returned by the latest image or prompt request.</p>
        <button type="button" disabled={pending} onClick={() => void download()}>Download STL</button>
      </section>
    </div>
    {state.error && <div class="notice" role="alert"><strong>Model issue</strong><span>{state.error.message}</span></div>}
    {confirmUploadOpen && <ConfirmDialog
      title="Upload a new drawing?"
      message="Your current project and any changes you've made will be deleted. This cannot be undone."
      confirmLabel="Delete and upload"
      onCancel={() => setConfirmUploadOpen(false)}
      onConfirm={() => { setConfirmUploadOpen(false); newFileInput.current?.click() }}
    />}
  </main>
}

function decodeStl(encoded: string): ArrayBuffer {
  const binary = atob(encoded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes.buffer
}

const OVERLAY_COLOR = 0x2a5c8a
const OVERLAY_SELECTED_COLOR = 0xff6a1a

function ModelViewer({ stl, features, selectedId, onSelect }: {
  stl: string; features: FeatureRosterEntry[]; selectedId: string | null; onSelect: (id: string | null) => void
}) {
  const mount = useRef<HTMLDivElement>(null)
  const overlays = useRef<Map<string, THREE.LineSegments>>(new Map())

  useEffect(() => {
    const element = mount.current
    if (!element) return
    const scene = new THREE.Scene(); scene.background = new THREE.Color('#f3f4f1')
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10000); camera.up.set(0, 0, 1)
    const renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace
    element.replaceChildren(renderer.domElement)
    const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true
    scene.add(new THREE.HemisphereLight(0xffffff, 0x52616d, 2.4))
    const light = new THREE.DirectionalLight(0xffffff, 2.5); light.position.set(3, -5, 6); scene.add(light)
    const geometry = new STLLoader().parse(decodeStl(stl)); geometry.computeVertexNormals(); geometry.computeBoundingBox()
    const bounds = geometry.boundingBox!; const zShift = -bounds.min.z; geometry.translate(0, 0, zShift); geometry.computeBoundingBox()
    const positioned = geometry.boundingBox!; const size = positioned.getSize(new THREE.Vector3()).length() || 1
    const material = new THREE.MeshStandardMaterial({ color: '#2a5c8a', metalness: 0.14, roughness: 0.48 }); scene.add(new THREE.Mesh(geometry, material))
    const guides = coordinateGuides(positioned); scene.add(guides)

    // Feature overlays: an invisible proxy per feature carries the raycast hit-test (Raycaster
    // ignores object.visible, so this renders nothing but still registers clicks); the paired
    // EdgesGeometry outline is what the user actually sees. Z is shifted to match the STL
    // translation above — X/Y stay in the untouched CAD frame (see coordinateGuides note below).
    const proxies: THREE.Mesh[] = []
    const overlayMap = new Map<string, THREE.LineSegments>()
    for (const feature of features) {
      if (!feature.extent) continue
      const [minX, minY, minZ] = feature.extent.minimum
      const [maxX, maxY, maxZ] = feature.extent.maximum
      const box = new THREE.BoxGeometry(Math.max(maxX - minX, 0.01), Math.max(maxY - minY, 0.01), Math.max(maxZ - minZ, 0.01))
      const center = new THREE.Vector3((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2 + zShift)
      const proxy = new THREE.Mesh(box, new THREE.MeshBasicMaterial({ visible: false }))
      proxy.position.copy(center); proxy.userData.featureId = feature.id
      scene.add(proxy); proxies.push(proxy)
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(box), new THREE.LineBasicMaterial({
        color: feature.id === selectedId ? OVERLAY_SELECTED_COLOR : OVERLAY_COLOR, transparent: true, opacity: feature.id === selectedId ? 0.95 : 0.55,
      }))
      outline.position.copy(center)
      scene.add(outline); overlayMap.set(feature.id, outline)
    }
    overlays.current = overlayMap

    const centre = positioned.getCenter(new THREE.Vector3()); const distance = Math.max(size * 1.6, 30)
    camera.position.set(centre.x + distance, centre.y - distance, centre.z + distance * .75); controls.target.copy(centre)

    const raycaster = new THREE.Raycaster()
    let pointerDownAt: { x: number; y: number } | null = null
    const onPointerDown = (event: PointerEvent) => { pointerDownAt = { x: event.clientX, y: event.clientY } }
    const onPointerUp = (event: PointerEvent) => {
      const start = pointerDownAt; pointerDownAt = null
      if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return // drag/orbit, not a click
      const rect = renderer.domElement.getBoundingClientRect()
      raycaster.setFromCamera(new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1), camera)
      const hit = raycaster.intersectObjects(proxies)[0]
      const id = (hit?.object.userData.featureId as string | undefined) ?? null
      onSelect(id)
      if (id) document.querySelector(`[data-feature-id="${id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointerup', onPointerUp)

    let frame = 0
    const resize = () => { const { width, height } = element.getBoundingClientRect(); renderer.setSize(width || 1, height || 1, false); camera.aspect = (width || 1) / (height || 1); camera.updateProjectionMatrix() }
    const observer = new ResizeObserver(resize); observer.observe(element); resize()
    const animate = () => { frame = requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera) }; animate()
    return () => {
      cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); disposeGuides(guides)
      renderer.domElement.removeEventListener('pointerdown', onPointerDown); renderer.domElement.removeEventListener('pointerup', onPointerUp)
      geometry.dispose(); material.dispose(); renderer.dispose()
      for (const proxy of proxies) { proxy.geometry.dispose(); (proxy.material as THREE.Material).dispose() }
      for (const outline of overlayMap.values()) { outline.geometry.dispose(); (outline.material as THREE.Material).dispose() }
      overlays.current = new Map()
    }
  }, [stl, features])

  // Recolors existing overlays on selection change alone — no scene rebuild, no camera jump.
  useEffect(() => {
    for (const [id, outline] of overlays.current) {
      const isSelected = id === selectedId
      const lineMaterial = outline.material as THREE.LineBasicMaterial
      lineMaterial.color.set(isSelected ? OVERLAY_SELECTED_COLOR : OVERLAY_COLOR)
      lineMaterial.opacity = isSelected ? 0.95 : 0.55
    }
  }, [selectedId])

  return <div class="model-stage"><div ref={mount} class="model-canvas" aria-label="Interactive 3D model viewer with coordinate rulers" /><p class="model-help">XY plane at Z=0 · Drag to rotate · Scroll to zoom · Click a feature to select it</p></div>
}

function coordinateGuides(bounds: THREE.Box3): THREE.Group {
  const group = new THREE.Group(); const span = Math.max(bounds.max.x, bounds.max.y, bounds.max.z, 10); const step = rulerStep(span); const size = Math.ceil(span / step) * step
  const grid = new THREE.GridHelper(size, Math.max(2, Math.round(size / step)), 0xaab8b0, 0xd5ddd7); grid.rotation.x = Math.PI / 2; grid.position.set(size / 2, size / 2, 0); group.add(grid)
  for (const [axis, color] of [[new THREE.Vector3(1, 0, 0), 0xc74545], [new THREE.Vector3(0, 1, 0), 0x3f8d61], [new THREE.Vector3(0, 0, 1), 0x3a6f9f]] as const) group.add(new THREE.ArrowHelper(axis, new THREE.Vector3(), size * .58, color))
  return group
}

function rulerStep(span: number): number { const target = Math.max(span / 8, 1); const base = 10 ** Math.floor(Math.log10(target)); return [1, 2, 5, 10].map((factor) => factor * base).find((step) => step >= target) || base }
function disposeGuides(group: THREE.Group): void { group.traverse((object) => { if (object instanceof THREE.Line || object instanceof THREE.LineSegments) object.geometry.dispose() }) }

function App() { return useAppStore((state) => state.specification) ? <Workspace /> : <UploadScreen /> }
render(<App />, document.getElementById('app')!)
