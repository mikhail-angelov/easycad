// Three.js STL viewer used by the Viewer panel. Encapsulates scene setup,
// orbit controls, a ground grid, lighting, and base64-STL loading with
// automatic camera framing. CadQuery exports Z-up; we rotate to Y-up so the
// model sits naturally on the grid.

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { STLLoader } from 'three/addons/loaders/STLLoader.js'

export class ModelViewer {
  private scene = new THREE.Scene()
  private camera: THREE.PerspectiveCamera
  private renderer: THREE.WebGLRenderer
  private controls: OrbitControls
  private grid: THREE.GridHelper
  private loader = new STLLoader()
  private mesh: THREE.Mesh | null = null
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
    container.appendChild(this.renderer.domElement)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true

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

  setWireframe(on: boolean) {
    this.wireframe = on
    if (this.mesh) (this.mesh.material as THREE.MeshStandardMaterial).wireframe = on
  }

  clear() {
    this.disposeMesh()
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
    if (!this.mesh) return
    this.scene.remove(this.mesh)
    this.mesh.geometry.dispose()
    ;(this.mesh.material as THREE.Material).dispose()
    this.mesh = null
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
