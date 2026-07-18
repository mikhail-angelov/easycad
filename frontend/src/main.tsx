import { render, type TargetedEvent } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { useAppStore } from './store'
import type { ApiError, ModelResponse } from './types'
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

function UploadScreen() {
  const input = useRef<HTMLInputElement>(null)
  const state = useAppStore()
  const upload = async (file: File) => {
    state.setRequestState('working')
    try {
      const form = new FormData()
      form.append('file', file)
      state.setModelResponse(await requestJson<ModelResponse>('/api/model/image', { method: 'POST', body: form }))
    } catch (error) { state.setError(error as ApiError) }
    finally { state.setRequestState('idle') }
  }
  const change = (event: TargetedEvent<HTMLInputElement, Event>) => {
    const file = event.currentTarget.files?.[0]
    if (file) void upload(file)
  }
  return <main class="upload-page"><div class="upload-card">
    <div class="brand"><span class="brand-mark" aria-hidden="true" /> EasyCAD</div>
    <p class="eyebrow">Drawing to 3D model</p>
    <h1>Upload a drawing.</h1>
    <p class="intro">EasyCAD returns a minimal reliable model immediately. You can then describe changes in plain text.</p>
    <input ref={input} class="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={change} />
    <button class="upload-target" type="button" disabled={state.requestState === 'working'} onClick={() => input.current?.click()}>
      {state.requestState === 'working' ? <><span class="spinner" /><span><strong>Building your model…</strong><small>Reading the drawing and rendering 3D geometry.</small></span></> : <><strong>Choose a drawing</strong><span>PNG, JPEG, or WebP</span></>}
    </button>
    {state.error && <p class="field-error">{state.error.message}</p>}
  </div></main>
}

function Workspace() {
  const state = useAppStore()
  const [prompt, setPrompt] = useState('')
  const pending = state.requestState === 'working'
  const refine = async () => {
    if (!state.specification || !prompt.trim()) return
    state.setRequestState('working')
    try {
      state.setModelResponse(await requestJson<ModelResponse>('/api/model/refine', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specification: state.specification, prompt }),
      }))
      setPrompt('')
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
    <header class="topbar"><div class="brand"><span class="brand-mark" /> EasyCAD <span class="project-name">{state.specification?.title}</span></div><button class="text-button" type="button" onClick={state.reset}>Start over</button></header>
    <section class="headline"><p class="eyebrow">Current 3D model</p><h1>{state.description}</h1><p>The model stays visible while you refine it.</p></section>
    <div class="workspace"><section class="drawing-panel"><ModelViewer stl={state.modelStl!} /></section>
      <section class="review-panel"><div class="review-heading"><div><p class="eyebrow">Change the model</p><h2>Describe the next change</h2></div></div>
        <label class="question-clarification">Freeform instruction
          <textarea value={prompt} placeholder={'For example: “remove the top cylinder” or “the top-hole diameter is 15 mm.”'} onInput={(event) => setPrompt(event.currentTarget.value)} />
        </label>
        <button class="primary" type="button" disabled={pending || !prompt.trim()} onClick={() => void refine()}>{pending ? 'Updating model…' : 'Apply change'}</button>
        <hr />
        <p class="section-note">The displayed model is the exact STL returned by the latest image or prompt request.</p>
        <button type="button" disabled={pending} onClick={() => void download()}>Download STL</button>
      </section>
    </div>
    {state.error && <div class="notice" role="alert"><strong>Model issue</strong><span>{state.error.message}</span></div>}
  </main>
}

function decodeStl(encoded: string): ArrayBuffer {
  const binary = atob(encoded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes.buffer
}

function ModelViewer({ stl }: { stl: string }) {
  const mount = useRef<HTMLDivElement>(null)
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
    const bounds = geometry.boundingBox!; geometry.translate(0, 0, -bounds.min.z); geometry.computeBoundingBox()
    const positioned = geometry.boundingBox!; const size = positioned.getSize(new THREE.Vector3()).length() || 1
    const material = new THREE.MeshStandardMaterial({ color: '#2a5c8a', metalness: 0.14, roughness: 0.48 }); scene.add(new THREE.Mesh(geometry, material))
    const guides = coordinateGuides(positioned); scene.add(guides)
    const centre = positioned.getCenter(new THREE.Vector3()); const distance = Math.max(size * 1.6, 30)
    camera.position.set(centre.x + distance, centre.y - distance, centre.z + distance * .75); controls.target.copy(centre)
    let frame = 0
    const resize = () => { const { width, height } = element.getBoundingClientRect(); renderer.setSize(width || 1, height || 1, false); camera.aspect = (width || 1) / (height || 1); camera.updateProjectionMatrix() }
    const observer = new ResizeObserver(resize); observer.observe(element); resize()
    const animate = () => { frame = requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera) }; animate()
    return () => { cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); disposeGuides(guides); geometry.dispose(); material.dispose(); renderer.dispose() }
  }, [stl])
  return <div class="model-stage"><div ref={mount} class="model-canvas" aria-label="Interactive 3D model viewer with coordinate rulers" /><p class="model-help">XY plane at Z=0 · Drag to rotate · Scroll to zoom</p></div>
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
