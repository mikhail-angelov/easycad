// Three.js STL viewer used by the Viewer panel. Encapsulates scene setup,
// orbit controls, a ground grid, lighting, and base64-STL loading with
// automatic camera framing. CadQuery exports Z-up; we rotate to Y-up so the
// model sits naturally on the grid.

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { STLLoader } from 'three/addons/loaders/STLLoader.js'
import type { FaceMesh } from './api'

export class ModelViewer {
  private scene = new THREE.Scene()
  private camera: THREE.PerspectiveCamera
  private renderer: THREE.WebGLRenderer
  private controls: OrbitControls
  private grid: THREE.GridHelper
  private loader = new STLLoader()
  private mesh: THREE.Mesh | null = null
  private faceMesh: FaceMesh | null = null
  private selectedOverlay: THREE.Mesh | null = null
  private selectedLabel: THREE.Sprite | null = null
  private pickerLabels: THREE.Sprite[] = []
  private onFacePick: ((faceId: number) => void) | null = null
  private wireframe = false
  private raf = 0
  private ro: ResizeObserver

  constructor(private container: HTMLElement) {
    const w = container.clientWidth || 1
    const h = container.clientHeight || 1

    this.camera = new THREE.PerspectiveCamera(45, w / h, 0.5, 5000)
    this.camera.position.set(120, 90, 140)

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    this.renderer.setPixelRatio(window.devicePixelRatio)
    this.renderer.setSize(w, h)
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.domElement.id = 'model-canvas'
    this.renderer.domElement.dataset.testid = 'model-canvas'
    this.renderer.domElement.setAttribute('aria-label', '3D model viewer')
    container.appendChild(this.renderer.domElement)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.renderer.domElement.addEventListener('click', (event) => this.pickFace(event))

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.65))
    const key = new THREE.DirectionalLight(0xffffff, 0.9)
    key.position.set(1, 1.5, 1)
    this.scene.add(key)
    const fill = new THREE.DirectionalLight(0xffffff, 0.35)
    fill.position.set(-1, -0.4, -1)
    this.scene.add(fill)

    this.grid = new THREE.GridHelper(400, 40, 0xc7ccc4, 0xd8dad2)
    this.scene.add(this.grid)

    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(container)

    const loop = () => {
      this.raf = requestAnimationFrame(loop)
      this.controls.update()
      this.renderer.render(this.scene, this.camera)
    }
    loop()
  }

  private resize() {
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    if (!w || !h) return
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h)
  }

  setSTL(base64: string) {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
    const geo = this.loader.parse(bytes.buffer)
    geo.rotateX(-Math.PI / 2) // CadQuery Z-up -> three.js Y-up
    geo.computeVertexNormals()
    geo.center()

    this.disposeMesh()
    this.faceMesh = null
    const material = new THREE.MeshStandardMaterial({
      color: 0x2a5c8a,
      metalness: 0.1,
      roughness: 0.6,
      wireframe: this.wireframe,
      flatShading: true,
    })
    this.mesh = new THREE.Mesh(geo, material)
    this.scene.add(this.mesh)
    this.frame(geo)
  }

  setFaceMesh(data: FaceMesh) {
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3))
    geo.setIndex(data.indices)
    geo.rotateX(-Math.PI / 2) // CAD Z-up -> viewer Y-up
    geo.computeVertexNormals()
    geo.computeBoundingBox()
    const displayCentre = geo.boundingBox!.getCenter(new THREE.Vector3())
    geo.center()

    this.disposeMesh()
    this.faceMesh = data
    const material = new THREE.MeshStandardMaterial({
      color: 0x2a5c8a, metalness: 0.1, roughness: 0.6,
      wireframe: this.wireframe, flatShading: true,
    })
    this.mesh = new THREE.Mesh(geo, material)
    this.mesh.userData.displayCentre = displayCentre
    this.scene.add(this.mesh)
    this.frame(geo)
  }

  setFacePicking(onPick: ((faceId: number) => void) | null) {
    this.onFacePick = onPick
    this.renderer.domElement.style.cursor = onPick ? 'crosshair' : ''
    this.disposePickerLabels()
    if (onPick) this.showPickerLabels()
  }

  selectFace(faceId: number | null) {
    this.disposeSelection()
    if (faceId == null || !this.mesh || !this.faceMesh) return
    const face = this.faceMesh.faces.find((item) => item.id === faceId)
    if (!face) return
    const base = this.mesh.geometry as THREE.BufferGeometry
    const overlay = new THREE.BufferGeometry()
    overlay.setAttribute('position', base.getAttribute('position'))
    overlay.setIndex(this.faceMesh.indices.slice(face.start, face.start + face.count))
    this.selectedOverlay = new THREE.Mesh(overlay, new THREE.MeshBasicMaterial({
      color: 0xffc857, transparent: true, opacity: 0.58, side: THREE.DoubleSide,
      depthWrite: false,
    }))
    this.selectedOverlay.renderOrder = 1
    this.scene.add(this.selectedOverlay)

    const anchor = this.displayAnchor(face.anchor)
    this.selectedLabel = this.makeLabel(face.label)
    this.selectedLabel.position.copy(anchor)
    this.scene.add(this.selectedLabel)
  }

  setWireframe(on: boolean) {
    this.wireframe = on
    if (this.mesh) (this.mesh.material as THREE.MeshStandardMaterial).wireframe = on
  }

  clear() {
    this.disposeMesh()
    this.faceMesh = null
  }

  private pickFace(event: MouseEvent) {
    if (!this.onFacePick || !this.mesh || !this.faceMesh) return
    const rect = this.renderer.domElement.getBoundingClientRect()
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    )
    const raycaster = new THREE.Raycaster()
    raycaster.setFromCamera(pointer, this.camera)
    const hit = raycaster.intersectObject(this.mesh, false)[0]
    if (!hit || hit.faceIndex == null) return
    const indexOffset = hit.faceIndex * 3
    const face = this.faceMesh.faces.find((item) =>
      indexOffset >= item.start && indexOffset < item.start + item.count,
    )
    if (face) this.onFacePick(face.id)
  }

  private makeLabel(text: string) {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 96
    const context = canvas.getContext('2d')!
    context.beginPath(); context.arc(48, 48, 34, 0, Math.PI * 2)
    context.fillStyle = '#ffc857'; context.fill()
    context.fillStyle = '#172033'; context.font = 'bold 48px sans-serif'
    context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillText(text, 48, 50)
    // Labels behind the model must remain hidden; otherwise A on the back face
    // can visually overlap a different front face and make the target ambiguous.
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthWrite: false }))
    sprite.scale.set(12, 12, 1)
    return sprite
  }

  private displayAnchor(anchor: [number, number, number]) {
    return new THREE.Vector3(...anchor)
      .applyAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2)
      .sub(this.mesh!.userData.displayCentre as THREE.Vector3)
  }

  private showPickerLabels() {
    if (!this.mesh || !this.faceMesh) return
    for (const face of this.faceMesh.faces.filter((item) => item.planar && item.center != null).slice(0, 30)) {
      const label = this.makeLabel(face.label)
      label.position.copy(this.displayAnchor(face.anchor))
      this.pickerLabels.push(label)
      this.scene.add(label)
    }
  }

  private frame(geo: THREE.BufferGeometry) {
    geo.computeBoundingBox()
    geo.computeBoundingSphere()
    const box = geo.boundingBox!
    const radius = geo.boundingSphere?.radius || 50
    this.grid.position.y = box.min.y

    const dist = (radius / Math.sin((this.camera.fov * Math.PI) / 180 / 2)) * 1.15
    const dir = new THREE.Vector3(1, 0.85, 1).normalize()
    this.camera.position.copy(dir.multiplyScalar(dist))
    this.camera.near = Math.max(radius / 100, 0.1)
    this.camera.far = radius * 100
    this.camera.updateProjectionMatrix()
    this.controls.target.set(0, 0, 0)
    this.controls.update()
  }

  private disposeMesh() {
    this.disposePickerLabels()
    this.disposeSelection()
    if (!this.mesh) return
    this.scene.remove(this.mesh)
    this.mesh.geometry.dispose()
    ;(this.mesh.material as THREE.Material).dispose()
    this.mesh = null
  }

  private disposePickerLabels() {
    for (const label of this.pickerLabels) {
      this.scene.remove(label)
      ;(label.material as THREE.SpriteMaterial).map?.dispose()
      ;(label.material as THREE.Material).dispose()
    }
    this.pickerLabels = []
  }

  private disposeSelection() {
    if (this.selectedOverlay) {
      this.scene.remove(this.selectedOverlay)
      this.selectedOverlay.geometry.dispose()
      ;(this.selectedOverlay.material as THREE.Material).dispose()
      this.selectedOverlay = null
    }
    if (this.selectedLabel) {
      this.scene.remove(this.selectedLabel)
      ;(this.selectedLabel.material as THREE.SpriteMaterial).map?.dispose()
      ;(this.selectedLabel.material as THREE.Material).dispose()
      this.selectedLabel = null
    }
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.ro.disconnect()
    this.disposeMesh()
    this.controls.dispose()
    this.renderer.dispose()
    this.renderer.domElement.remove()
  }
}
