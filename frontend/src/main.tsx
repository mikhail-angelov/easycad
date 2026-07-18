import { render, type TargetedEvent } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { useAppStore } from './store'
import type { ApiError, Dimension, DraftSpecification, LintResult, Project, ReviewPlanItem } from './types'
import './styles.css'
import './model-viewer.css'

function errorFrom(response: unknown): ApiError {
  const detail = (response as { detail?: { message?: string; stage?: string; request_id?: string; detail?: { field_ids?: string[]; messages?: string[] } } })?.detail
  return {
    message: detail?.detail?.messages?.join('; ') || detail?.message || 'Something went wrong. Please try again.',
    fieldIds: detail?.detail?.field_ids || [],
    stage: detail?.stage,
    requestId: detail?.request_id,
  }
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw errorFrom(payload)
  return payload as T
}

function buildableSpecification(specification: DraftSpecification): DraftSpecification {
  return specification
}

function statusLabel(status: string): string {
  return ({ needs_input: 'Needs a value', assumed: 'Proposed value', conflicted: 'Conflict', confirmed: 'Confirmed', unsupported: 'Not included' }[status] || status)
}

function UploadScreen() {
  const input = useRef<HTMLInputElement>(null)
  const { requestState, reset, setError, setRequestState, setReviewData, setSourceUrl, setSpecification } = useAppStore()
  const busy = requestState === 'analyzing'

  const upload = async (file: File) => {
    reset()
    setSourceUrl(URL.createObjectURL(file))
    setRequestState('analyzing')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('input_mode', 'sketch')
      const result = await requestJson<{ specification: DraftSpecification; lint: LintResult; review_plan: ReviewPlanItem[] }>('/api/specifications/analyze', { method: 'POST', body: form })
      setSpecification(result.specification)
      setReviewData(result.lint, result.review_plan)
    } catch (error) {
      setError(error as ApiError)
    } finally {
      setRequestState('idle')
    }
  }

  const change = (event: TargetedEvent<HTMLInputElement, Event>) => {
    const file = event.currentTarget.files?.[0]
    if (file) void upload(file)
  }

  return <main class="upload-page">
    <div class="upload-card">
      <div class="brand"><span class="brand-mark" aria-hidden="true" /> EasyCAD</div>
      <p class="eyebrow">Drawing to printable model</p>
      <h1>Review a drawing before anything is built.</h1>
      <p class="intro">Upload a technical drawing. EasyCAD will identify dimensions and questions for you to confirm before creating an STL.</p>
      <input ref={input} class="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={change} />
      <button class="upload-target" type="button" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? <><span class="spinner" aria-hidden="true" /><span><strong>Analyzing your drawing…</strong><small>This can take a moment while EasyCAD reads the image.</small></span></> : <><strong>Choose a drawing</strong><span>PNG, JPEG, or WebP</span></>}
      </button>
      <p class="fine-print">Best results come from a drawing with dimensions and clear views.</p>
    </div>
  </main>
}

function DimensionRow({ dimension, index }: { dimension: Dimension; index: number }) {
  const { draftValues, error, selectedId, setDraftValue, setSelectedId } = useAppStore()
  const currentValue = draftValues[dimension.id] ?? dimension.value ?? ''
  const needsAction = dimension.status !== 'confirmed'
  const isAccepted = Object.hasOwn(draftValues, dimension.id)
  const editing = needsAction || isAccepted
  const numericValue = typeof currentValue === 'number' ? currentValue : Number(currentValue)
  const hasError = error?.fieldIds.includes(dimension.id)
  return <article id={`item-${dimension.id}`} class={`review-row ${selectedId === dimension.id ? 'selected' : ''} ${hasError ? 'has-error' : ''}`} onClick={() => setSelectedId(dimension.id)}>
    <span class={`number status-${dimension.status}`}>{index + 1}</span>
    <div class="review-copy">
      <strong>{dimension.label}</strong>
      <span>{statusLabel(dimension.status)}{dimension.evidence[0] ? ` · ${dimension.evidence[0]}` : ''}</span>
      {hasError && <span class="field-error">Needs attention</span>}
    </div>
    <div class="value-control">
      {editing ? <>
        <input aria-label={dimension.label} type="number" value={currentValue} min={dimension.min ?? undefined} max={dimension.max ?? undefined}
          placeholder="Enter value" onInput={(event) => setDraftValue(dimension.id, event.currentTarget.value === '' ? '' : Number(event.currentTarget.value))} />
        <span>{dimension.unit}</span>
        {dimension.status === 'assumed' && !isAccepted && Number.isFinite(numericValue) && <button type="button" class="text-button" onClick={() => setDraftValue(dimension.id, numericValue)}>Use value</button>}
      </> : <><output>{String(currentValue)}</output><span>{dimension.unit}</span><button type="button" class="text-button" onClick={() => setDraftValue(dimension.id, typeof dimension.value === 'number' ? dimension.value : String(dimension.value ?? ''))}>Edit</button></>}
    </div>
  </article>
}

function ReviewWorkspace() {
  const state = useAppStore()
  const spec = state.specification!
  const pending = state.requestState !== 'idle'
  const [schematic, setSchematic] = useState<Record<string, string>>({})

  useEffect(() => {
    void requestJson<{ views: Record<string, string> }>('/api/specifications/schematic', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec),
    }).then((result) => setSchematic(result.views)).catch(() => setSchematic({}))
  }, [spec])

  const validate = async () => {
    state.setRequestState('validating')
    state.setError(null)
    try {
      const result = await requestJson<{ valid: boolean; specification: DraftSpecification; lint: LintResult; review_plan: ReviewPlanItem[]; diagnostics?: { field_ids: string[]; messages: string[]; hints?: string[] } }>('/api/specifications/validate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specification: spec, dimension_values: state.draftValues, accepted_feature_ids: state.acceptedFeatureIds, accepted_assumption_ids: state.acceptedAssumptionIds, clarifications: state.clarifications }),
      })
      state.setSpecification(result.specification)
      state.setReviewData(result.lint, result.review_plan)
      state.setValidationPassed(result.valid)
      if (!result.valid) {
        const diagnostics = result.diagnostics || { field_ids: [], messages: ['Review the proposed clarification before building.'] }
        state.setError({ message: diagnostics.messages.join('; '), fieldIds: diagnostics.field_ids, hints: diagnostics.hints })
      }
    } catch (error) {
      const apiError = error as ApiError
      state.setError(apiError)
      state.setValidationPassed(false)
      if (apiError.fieldIds[0]) {
        state.setSelectedId(apiError.fieldIds[0])
        queueMicrotask(() => document.getElementById(`item-${apiError.fieldIds[0]}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
      }
    } finally {
      state.setRequestState('idle')
    }
  }

  const previewDraft = async () => {
    state.setRequestState('building')
    state.setError(null)
    try {
      const result = await requestJson<{ status: string; project: Project }>('/api/specifications/build?mode=draft', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildableSpecification(spec)) })
      if (result.status === 'draft_preview') state.setProject(result.project)
    } catch (error) { state.setError(error as ApiError) }
    finally { state.setRequestState('idle') }
  }

  useEffect(() => { void previewDraft() }, [spec])

  const excludeFeature = async (featureId: string) => {
    state.setRequestState('validating')
    try {
      const result = await requestJson<{ specification: DraftSpecification; lint: LintResult; review_plan: ReviewPlanItem[] }>('/api/specifications/validate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ specification: spec, excluded_feature_ids: [featureId] }),
      })
      state.setSpecification(result.specification); state.setReviewData(result.lint, result.review_plan)
    } catch (error) { state.setError(error as ApiError) }
    finally { state.setRequestState('idle') }
  }

  const applySuggestion = async (suggestion: NonNullable<LintResult['issues'][number]['suggestion']>) => {
    state.setRequestState('validating')
    try {
      const result = await requestJson<{ specification: DraftSpecification; lint: LintResult; review_plan: ReviewPlanItem[] }>('/api/specifications/validate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specification: spec, feature_field_edits: { [suggestion.feature_id]: { [suggestion.field_path]: suggestion.value } } }),
      })
      state.setSpecification(result.specification); state.setReviewData(result.lint, result.review_plan)
    } catch (error) { state.setError(error as ApiError) }
    finally { state.setRequestState('idle') }
  }

  const acceptTierThree = () => {
    for (const item of state.reviewPlan.filter((entry) => entry.tier === 3)) {
      if (item.item_type === 'feature' && !state.acceptedFeatureIds.includes(item.item_id)) state.toggleFeature(item.item_id)
      if (item.item_type === 'assumption' && !state.acceptedAssumptionIds.includes(item.item_id)) state.toggleAssumption(item.item_id)
      if (item.item_type === 'dimension') {
        const dimension = spec.dimensions.find((entry) => entry.id === item.item_id)
        if (dimension?.value != null) state.setDraftValue(dimension.id, dimension.value)
      }
    }
  }

  const renderPlanItem = (item: ReviewPlanItem) => {
    if (item.item_type === 'dimension') {
      const dimension = spec.dimensions.find((entry) => entry.id === item.item_id)
      return dimension ? <DimensionRow key={`dimension:${item.item_id}`} dimension={dimension} index={spec.dimensions.indexOf(dimension)} /> : null
    }
    if (item.item_type === 'question') {
      const question = spec.questions.find((entry) => entry.id === item.item_id)
      return question ? <QuestionRow key={`question:${item.item_id}`} question={question} /> : null
    }
    if (item.item_type === 'feature') {
      const feature = spec.features.find((entry) => entry.id === item.item_id)
      return feature ? <article class="review-row" id={`item-${feature.id}`} key={`feature:${feature.id}`} onMouseEnter={() => state.setSelectedId(feature.id)}><div class="review-copy"><strong>{feature.label}</strong><span>{statusLabel(feature.status)}</span></div>{feature.status === 'assumed' && <input aria-label={`Accept ${feature.label}`} type="checkbox" checked={state.acceptedFeatureIds.includes(feature.id)} onChange={() => state.toggleFeature(feature.id)} />}<button type="button" class="text-button" onClick={() => void excludeFeature(feature.id)}>Exclude</button></article> : null
    }
    if (item.item_type === 'assumption') {
      const assumption = spec.assumptions.find((entry) => entry.id === item.item_id)
      return assumption ? <label class="assumption" key={`assumption:${item.item_id}`}><input type="checkbox" checked={state.acceptedAssumptionIds.includes(assumption.id)} onChange={() => state.toggleAssumption(assumption.id)} /><span>{assumption.rationale}</span></label> : null
    }
    if (item.item_type === 'lint_issue') {
      const issue = state.lint.issues.find((entry) => entry.issue_id === item.item_id)
      return issue ? <p class="omitted" key={`lint:${item.item_id}`}>{issue.message}{issue.suggestion && <button type="button" class="text-button" onClick={() => void applySuggestion(issue.suggestion!)}>Apply</button>}</p> : null
    }
    const exclusion = (spec.exclusions || []).find((entry) => entry.feature_id === item.item_id)
    return exclusion ? <p class="omitted" key={`exclusion:${item.item_id}`}><strong>{exclusion.feature_id}</strong>: {exclusion.reason}</p> : null
  }

  const build = async () => {
    state.setRequestState('building')
    state.setError(null)
    try {
      const result = await requestJson<{ status: string; project: Project; repair_hints?: string[] }>('/api/specifications/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildableSpecification(spec)) })
      if (result.status === 'success') state.setProject(result.project)
      else {
        const generationError = result.project.generation?.error
        const mismatches = generationError?.detail?.mismatches || []
        state.setError({
          message: [generationError?.message || 'The model could not be built. Review the specification and try again.', ...mismatches].join('; '),
          fieldIds: generationError?.detail?.feature_ids || [],
          stage: generationError?.stage,
          hints: result.repair_hints || [],
        })
      }
    } catch (error) {
      state.setError(error as ApiError)
    } finally {
      state.setRequestState('idle')
    }
  }

  return <main class="app-shell">
    <header class="topbar"><div class="brand"><span class="brand-mark" aria-hidden="true" /> EasyCAD <span class="project-name">{spec.title} · {spec.units}</span></div><button class="text-button" type="button" onClick={state.reset}>Start over</button></header>
    <section class="headline"><p class="eyebrow">Minimal 3D model</p><h1>Review the first reliable model.</h1><p>Ambiguous drawing details are omitted. Describe any additions or changes in plain text.</p></section>
    <div class="workspace">
      <DrawingPanel specification={spec} project={state.project} schematic={schematic} />
      <section class="review-panel" aria-label="Specification review">
        <div class="review-heading"><div><p class="eyebrow">Model details</p><h2>Minimal reliable model</h2></div></div>
        {state.reviewPlan.some((item) => item.tier === 3) && <button type="button" class="text-button" onClick={acceptTierThree}>Accept all proposed items</button>}
        {Array.from(new Set(state.reviewPlan.map((item) => item.tier))).map((tier) => <ReviewSection title={`Tier ${tier}`} tone={tier === 2 || tier === 4 ? 'warning' : undefined} key={tier}>{state.reviewPlan.filter((item) => item.tier === tier).map(renderPlanItem)}</ReviewSection>)}
        <ReviewSection title="Change the model">
          <label class="question-clarification">Tell the model what to change
            <textarea value={state.clarifications.freeform_instruction || ''} placeholder="For example: “remove the top cylinder” or “the top-hole diameter is 15 mm.”" onInput={(event) => state.setClarification('freeform_instruction', event.currentTarget.value)} />
          </label>
          <p class="section-note">This instruction is sent whenever you validate or preview the revised draft.</p>
        </ReviewSection>
        {spec.features.some((feature) => feature.status === 'confirmed') && <ReviewSection title="Confirmed features">{spec.features.filter((feature) => feature.status === 'confirmed').map((feature) => <article class="review-row" id={`item-${feature.id}`} key={feature.id} onMouseEnter={() => state.setSelectedId(feature.id)}><div class="review-copy"><strong>{feature.label}</strong><span>Confirmed</span></div><button type="button" class="text-button" onClick={() => void excludeFeature(feature.id)}>Exclude</button></article>)}</ReviewSection>}
        {spec.features.some((feature) => feature.status === 'unsupported') && <ReviewSection title="Not included in the model" tone="warning">{spec.features.filter((feature) => feature.status === 'unsupported').map((feature) => <p class="omitted" key={feature.id}>{feature.label}</p>)}</ReviewSection>}
        {spec.dimensions.some((dimension) => dimension.status === 'confirmed') && <ReviewSection title="Confirmed dimensions">{spec.dimensions.filter((dimension) => dimension.status === 'confirmed').map((dimension) => <DimensionRow key={dimension.id} dimension={dimension} index={spec.dimensions.indexOf(dimension)} />)}</ReviewSection>}
        {Object.values(state.clarifications).some((text) => text.trim()) && <ReviewSection title="Your clarifications">
          {Object.entries(state.clarifications).filter(([, text]) => text.trim()).map(([questionId, text]) => <p class="omitted" key={questionId}><strong>{questionId}:</strong> {text}</p>)}
        </ReviewSection>}
        {state.error?.fieldIds.length && state.error.stage !== 'semantic_validation' && <ReviewSection title="Fix the highlighted detail" tone="warning"><p class="section-note">{state.error.hints?.join(' ')}</p>{state.error.fieldIds.map((fieldId) => <label class="question-clarification" key={fieldId}>Describe the intended value for <code>{fieldId}</code><textarea value={state.clarifications[`validation_repair:${fieldId}`] || ''} placeholder="For example: “place its center at X=30, Y=30, Z=20 mm.”" onInput={(event) => state.setClarification(`validation_repair:${fieldId}`, event.currentTarget.value)} /></label>)}<p class="section-note">Validate the specification to send this correction to DeepSeek and rebuild the complete draft.</p></ReviewSection>}
        {state.error?.stage === 'semantic_validation' && <ReviewSection title="Fix the build issue" tone="warning"><p class="section-note">{state.error.hints?.join(' ')}</p><label class="question-clarification">Describe the intended correction<textarea value={state.clarifications.build_repair || ''} placeholder="For example: “the groove runs along Y, starts at Y=0, and its center is on the top surface at Z=56.”" onInput={(event) => state.setClarification('build_repair', event.currentTarget.value)} /></label><p class="section-note">Then validate the specification to replan it with DeepSeek.</p></ReviewSection>}
        {spec.assumptions.filter((assumption) => assumption.status === 'confirmed').length > 0 && <ReviewSection title="Confirmed decisions" count={String(spec.assumptions.filter((assumption) => assumption.status === 'confirmed').length)}>
          {spec.assumptions.filter((assumption) => assumption.status === 'confirmed').map((assumption) => <p class="confirmed-decision" key={assumption.id}><strong>Confirmed</strong>{assumption.rationale}</p>)}
        </ReviewSection>}
      </section>
    </div>
    {state.error && <div class="notice" role="alert"><strong>{state.error.fieldIds.length ? 'Please review the highlighted items.' : 'Build issue'}</strong><span>{state.error.message}{state.error.stage ? ` (stage: ${state.error.stage})` : ''}{state.error.requestId ? ` · reference ${state.error.requestId}` : ''}</span></div>}
    <ActionBar pending={pending} validated={state.validationPassed} onValidate={validate} onPreview={() => void previewDraft()} onBuild={build} />
    {state.project && <Preview project={state.project} />}
  </main>
}

function QuestionRow({ question }: { question: DraftSpecification['questions'][number] }) {
  const state = useAppStore()
  const specification = state.specification!
  const answerAlternative = (alternative: number | string) => {
    if (specification.dimensions.some((dimension) => dimension.id === question.field_id)) state.setDraftValue(question.field_id, alternative)
    else state.setClarification(question.id, `The answer is: ${String(alternative)}.`)
  }
  return <div class="question" id={`item-${question.field_id}`}><strong>{question.prompt}</strong><div class="choices">{(question.alternatives || []).map((alternative) => <button type="button" key={String(alternative)} onClick={() => answerAlternative(alternative)}>{String(alternative)} {typeof alternative === 'number' ? 'mm' : ''}</button>)}</div><label class="question-clarification">Add a clarification<textarea value={state.clarifications[question.id] || ''} placeholder="Describe this detail, for example: “the hole is centered on the plate.”" onInput={(event) => state.setClarification(question.id, event.currentTarget.value)} /></label></div>
}

function ReviewSection({ title, count, tone, children }: { title: string; count?: string; tone?: string; children: preact.ComponentChildren }) {
  return <section class={`review-section ${tone || ''}`}><header><h3>{title}</h3>{count && <span>{count}</span>}</header>{children}</section>
}

function DrawingPanel({ specification, project, schematic }: { specification: DraftSpecification; project: Project | null; schematic: Record<string, string> }) {
  const { selectedId, setSelectedId, sourceUrl } = useAppStore()
  const [tab, setTab] = useState<'source' | 'schematic' | 'model'>('source')
  useEffect(() => { if (project) setTab('model') }, [project])
  useEffect(() => {
    document.querySelectorAll('[data-feature-id]').forEach((element) => element.setAttribute('data-selected', String(element.getAttribute('data-feature-id') === selectedId)))
  }, [selectedId, schematic, tab])
  return <section class="drawing-panel">
    <header class="drawing-header"><div class="panel-tabs" role="tablist" aria-label="Preview"><button type="button" role="tab" aria-selected={tab === 'source'} class={tab === 'source' ? 'active' : ''} onClick={() => setTab('source')}>Source drawing</button><button type="button" role="tab" aria-selected={tab === 'schematic'} class={tab === 'schematic' ? 'active' : ''} onClick={() => setTab('schematic')}>Schematic</button><button type="button" role="tab" aria-selected={tab === 'model'} class={tab === 'model' ? 'active' : ''} disabled={!project} onClick={() => setTab('model')}>3D model</button></div><span>{tab === 'model' ? 'Drag to rotate · Scroll to zoom' : 'Review markers'}</span></header>
    {tab === 'model' && project ? <ModelViewer project={project} /> : tab === 'schematic' ? <div class="drawing-stage schematic-views" onMouseOver={(event) => { const id = (event.target as Element).getAttribute?.('data-feature-id'); if (id) setSelectedId(id) }}>{Object.entries(schematic).map(([view, svg]) => <figure key={view} class={selectedId ? 'has-selection' : ''}><div dangerouslySetInnerHTML={{ __html: svg }} /><figcaption>{view}</figcaption></figure>)}</div> : <div class="drawing-stage">{sourceUrl ? <img src={sourceUrl} alt="Uploaded technical drawing" /> : <div class="drawing-empty">Drawing preview unavailable</div>}{specification.annotations.map((annotation, index) => <button type="button" class={`marker ${selectedId === annotation.field_id ? 'selected' : ''}`} style={{ left: `${annotation.x * 100}%`, top: `${annotation.y * 100}%` }} onClick={() => { setSelectedId(annotation.field_id); document.getElementById(`item-${annotation.field_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }} aria-label={`Show ${annotation.label}`} key={annotation.id}>{index + 1}</button>)}</div>}
    {tab === 'source' && <footer><span><i class="legend confirmed" /> Confirmed</span><span><i class="legend proposed" /> Needs review</span><span><i class="legend conflict" /> Conflict</span></footer>}
  </section>
}

function rulerStep(span: number): number {
  const target = Math.max(span / 8, 1)
  const base = 10 ** Math.floor(Math.log10(target))
  return [1, 2, 5, 10].map((factor) => factor * base).find((step) => step >= target) || base
}

function rulerLabel(text: string, position: THREE.Vector3, scale: number): THREE.Sprite {
  const canvas = document.createElement('canvas')
  canvas.width = 160
  canvas.height = 48
  const context = canvas.getContext('2d')!
  context.font = '24px ui-monospace, SFMono-Regular, Menlo, monospace'
  context.textAlign = 'center'
  context.textBaseline = 'middle'
  context.fillStyle = '#243746'
  context.fillText(text, 80, 24)
  const material = new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), transparent: true, depthTest: false })
  const sprite = new THREE.Sprite(material)
  sprite.position.copy(position)
  sprite.scale.set(14 * scale, 4.2 * scale, 1)
  return sprite
}

function coordinateGuides(bounds: THREE.Box3): THREE.Group {
  const group = new THREE.Group()
  const minX = Math.min(0, bounds.min.x)
  const minY = Math.min(0, bounds.min.y)
  const maxX = Math.max(0, bounds.max.x)
  const maxY = Math.max(0, bounds.max.y)
  const maxZ = Math.max(0, bounds.max.z)
  const span = Math.max(maxX - minX, maxY - minY, maxZ, 10)
  const step = rulerStep(span)
  const gridSize = Math.ceil(span / step) * step
  const scale = Math.max(gridSize / 80, 0.55)
  const grid = new THREE.GridHelper(gridSize, Math.max(2, Math.round(gridSize / step)), 0xaab8b0, 0xd5ddd7)
  grid.rotation.x = Math.PI / 2
  grid.position.set((minX + maxX) / 2, (minY + maxY) / 2, 0)
  group.add(grid)

  const axisLength = gridSize * 0.58
  const origin = new THREE.Vector3(0, 0, 0)
  const arrows = [
    [new THREE.Vector3(1, 0, 0), 0xc74545, 'X'],
    [new THREE.Vector3(0, 1, 0), 0x3f8d61, 'Y'],
    [new THREE.Vector3(0, 0, 1), 0x3a6f9f, 'Z'],
  ] as const
  for (const [direction, color, label] of arrows) {
    group.add(new THREE.ArrowHelper(direction, origin, axisLength, color, gridSize * 0.04, gridSize * 0.022))
    group.add(rulerLabel(label, direction.clone().multiplyScalar(axisLength + gridSize * 0.06), scale))
  }

  const tickMaterial = new THREE.LineBasicMaterial({ color: 0x65756b, transparent: true, opacity: 0.8 })
  const tickVertices: number[] = []
  const firstX = Math.ceil(minX / step) * step
  const firstY = Math.ceil(minY / step) * step
  for (let value = firstX; value <= maxX + step * 0.01; value += step) {
    tickVertices.push(value, 0, 0, value, gridSize * 0.025, 0)
    if (Math.abs(value) > step * 0.01) group.add(rulerLabel(`${Number(value.toFixed(6))} mm`, new THREE.Vector3(value, -gridSize * 0.06, 0), scale * 0.72))
  }
  for (let value = firstY; value <= maxY + step * 0.01; value += step) {
    tickVertices.push(0, value, 0, gridSize * 0.025, value, 0)
    if (Math.abs(value) > step * 0.01) group.add(rulerLabel(`${Number(value.toFixed(6))} mm`, new THREE.Vector3(-gridSize * 0.08, value, 0), scale * 0.72))
  }
  if (tickVertices.length) {
    const ticks = new THREE.BufferGeometry()
    ticks.setAttribute('position', new THREE.Float32BufferAttribute(tickVertices, 3))
    group.add(new THREE.LineSegments(ticks, tickMaterial))
  }
  group.add(rulerLabel('XY · Z = 0', new THREE.Vector3(minX, minY - gridSize * 0.12, 0), scale))
  return group
}

function disposeGuides(group: THREE.Group): void {
  group.traverse((object) => {
    if (object instanceof THREE.Sprite) {
      object.material.map?.dispose()
      object.material.dispose()
    }
    if (object instanceof THREE.Line || object instanceof THREE.LineSegments) {
      object.geometry.dispose()
      if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose())
      else object.material.dispose()
    }
  })
}

function ModelViewer({ project }: { project: Project }) {
  const mount = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const element = mount.current
    if (!element) return
    setError(null)
    const controller = new AbortController()
    const scene = new THREE.Scene()
    scene.background = new THREE.Color('#f3f4f1')
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10000)
    camera.up.set(0, 0, 1)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    element.replaceChildren(renderer.domElement)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    scene.add(new THREE.HemisphereLight(0xffffff, 0x52616d, 2.4))
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.5)
    keyLight.position.set(3, -5, 6)
    scene.add(keyLight)
    const fillLight = new THREE.DirectionalLight(0xb9d8ee, 1.2)
    fillLight.position.set(-4, 2, 3)
    scene.add(fillLight)
    let frame = 0
    let geometry: THREE.BufferGeometry | null = null
    let material: THREE.MeshStandardMaterial | null = null
    let guides: THREE.Group | null = null
    const resize = () => {
      const { width, height } = element.getBoundingClientRect()
      renderer.setSize(width || 1, height || 1, false)
      camera.aspect = (width || 1) / (height || 1)
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(element)
    resize()
    const animate = () => { frame = requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera) }
    animate()
    void (async () => {
      try {
        const response = await fetch('/api/projects/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project, parameters: {} }), signal: controller.signal })
        if (!response.ok) throw errorFrom(await response.json().catch(() => ({})))
        geometry = new STLLoader().parse(await response.arrayBuffer())
        geometry.computeVertexNormals()
        const bounds = geometry.boundingBox || (geometry.computeBoundingBox(), geometry.boundingBox)
        if (!bounds) throw new Error('The 3D model has no bounds.')
        geometry.translate(0, 0, -bounds.min.z)
        geometry.computeBoundingBox()
        const positionedBounds = geometry.boundingBox!
        const size = positionedBounds.getSize(new THREE.Vector3()).length() || 1
        material = new THREE.MeshStandardMaterial({ color: '#2a5c8a', metalness: 0.14, roughness: 0.48 })
        scene.add(new THREE.Mesh(geometry, material))
        guides = coordinateGuides(positionedBounds)
        scene.add(guides)
        const distance = Math.max(size * 1.6, 30)
        const centre = positionedBounds.getCenter(new THREE.Vector3())
        camera.position.set(centre.x + distance, centre.y - distance, centre.z + distance * 0.75)
        controls.target.copy(centre)
        controls.update()
      } catch (cause) {
        if (!controller.signal.aborted) setError((cause as ApiError).message || 'Unable to load the 3D model.')
      }
    })()
    return () => {
      controller.abort()
      cancelAnimationFrame(frame)
      observer.disconnect()
      controls.dispose()
      if (guides) disposeGuides(guides)
      geometry?.dispose()
      material?.dispose()
      renderer.dispose()
    }
  }, [project])
  return <div class="model-stage"><div ref={mount} class="model-canvas" aria-label="Interactive 3D model viewer with X, Y, and Z coordinate rulers" />{error && <p class="model-error">{error}</p>}<p class="model-help">XY plane at Z=0 · Drag to rotate · Scroll to zoom · Right-drag to pan</p></div>
}

function ActionBar({ pending, validated, onValidate, onPreview, onBuild }: { pending: boolean; validated: boolean; onValidate: () => void; onPreview: () => void; onBuild: () => void }) {
  return <footer class="action-bar"><div><strong>{validated ? 'Specification validated' : 'Minimal model is ready'}</strong><span>{validated ? 'You can now build a 3D model.' : 'Preview is generated automatically; describe changes in plain text when needed.'}</span></div><div class="actions"><button type="button" disabled={pending} onClick={onValidate}>{pending ? 'Working…' : 'Apply changes'}</button><button type="button" disabled={pending} onClick={onPreview}>Preview</button><button type="button" class="primary" disabled={!validated || pending} onClick={onBuild}>Build 3D</button></div></footer>
}

function Preview({ project }: { project: Project }) {
  const image = project.generation?.render_artifacts?.isometric?.image_data || Object.values(project.generation?.render_artifacts || {})[0]?.image_data
  const setError = useAppStore((state) => state.setError)
  const download = async () => {
    try {
      const response = await fetch('/api/projects/export?format=stl', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project, parameters: {} }) })
      if (!response.ok) throw errorFrom(await response.json().catch(() => ({})))
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${project.id}.stl`; link.click(); URL.revokeObjectURL(url)
    } catch (error) {
      setError(error as ApiError)
    }
  }
  const isDraft = project.generation?.semantic_status === 'draft_preview'
  return <section class="preview"><div><p class="eyebrow">3D preview</p><h2>{isDraft ? 'Draft geometry preview' : 'Your printable model is ready.'}</h2><p>{isDraft ? 'Informational only. Validate and build before export.' : 'Review the rendered model, then download the STL for your slicer.'}</p>{!isDraft && <button class="primary" type="button" onClick={() => void download()}>Download STL</button>}</div>{image && <img src={image} alt="Rendered CAD model" />}</section>
}

function App() {
  const specification = useAppStore((state) => state.specification)
  const error = useAppStore((state) => state.error)
  return <>{specification ? <ReviewWorkspace /> : <UploadScreen />}{!specification && error && <div class="upload-error" role="alert">{error.message}{error.stage ? ` (stage: ${error.stage})` : ''}{error.requestId ? ` · reference ${error.requestId}` : ''}</div>}</>
}

render(<App />, document.getElementById('app')!)
