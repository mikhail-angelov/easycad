import { render, type TargetedEvent } from 'preact'
import { useRef } from 'preact/hooks'
import { useAppStore } from './store'
import type { ApiError, Dimension, DraftSpecification, Project } from './types'
import './styles.css'

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
  return { ...specification, features: specification.features.filter((feature) => feature.status !== 'unsupported') }
}

function statusLabel(status: string): string {
  return ({ needs_input: 'Needs a value', assumed: 'Proposed value', conflicted: 'Conflict', confirmed: 'Confirmed', unsupported: 'Not included' }[status] || status)
}

function UploadScreen() {
  const input = useRef<HTMLInputElement>(null)
  const { requestState, reset, setError, setRequestState, setSourceUrl, setSpecification } = useAppStore()
  const busy = requestState === 'analyzing'

  const upload = async (file: File) => {
    reset()
    setSourceUrl(URL.createObjectURL(file))
    setRequestState('analyzing')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('input_mode', 'sketch')
      const result = await requestJson<{ specification: DraftSpecification }>('/api/specifications/analyze', { method: 'POST', body: form })
      setSpecification(result.specification)
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
      {needsAction ? <>
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
  const blockers = spec.dimensions.filter((item) => item.status !== 'confirmed').length
    + spec.features.filter((item) => item.status !== 'confirmed' && item.status !== 'unsupported').length
    + spec.assumptions.filter((item) => item.status === 'assumed' && !state.acceptedAssumptionIds.includes(item.id)).length
  const omitted = spec.features.filter((item) => item.status === 'unsupported')
  const pending = state.requestState !== 'idle'

  const validate = async () => {
    state.setRequestState('validating')
    state.setError(null)
    try {
      const result = await requestJson<{ valid: boolean; specification: DraftSpecification; diagnostics?: { field_ids: string[]; messages: string[] } }>('/api/specifications/validate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specification: spec, dimension_values: state.draftValues, accepted_feature_ids: state.acceptedFeatureIds, accepted_assumption_ids: state.acceptedAssumptionIds, clarifications: state.clarifications }),
      })
      state.setSpecification(result.specification)
      state.setValidationPassed(result.valid)
      if (!result.valid) {
        const diagnostics = result.diagnostics || { field_ids: [], messages: ['Review the proposed clarification before building.'] }
        state.setError({ message: diagnostics.messages.join('; '), fieldIds: diagnostics.field_ids })
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
    <section class="headline"><p class="eyebrow">Draft specification</p><h1>Confirm the model before it is built.</h1><p>Resolve the highlighted items. Your drawing stays visible while you review every decision.</p></section>
    <div class="workspace">
      <DrawingPanel specification={spec} />
      <section class="review-panel" aria-label="Specification review">
        <div class="review-heading"><div><p class="eyebrow">Review specification</p><h2>{blockers ? `${blockers} item${blockers === 1 ? '' : 's'} need attention` : 'Ready to validate'}</h2></div></div>
        <ReviewSection title="Dimensions" count={`${spec.dimensions.filter((item) => item.status === 'confirmed').length} of ${spec.dimensions.length} confirmed`}>
          {spec.dimensions.map((dimension, index) => <DimensionRow key={dimension.id} dimension={dimension} index={index} />)}
        </ReviewSection>
        {spec.questions.length > 0 && <ReviewSection title="Questions" count={String(spec.questions.length)}>
          {spec.questions.map((question) => <QuestionRow key={question.id} question={question} />)}
        </ReviewSection>}
        {Object.values(state.clarifications).some((text) => text.trim()) && <ReviewSection title="Your clarifications">
          {Object.entries(state.clarifications).filter(([, text]) => text.trim()).map(([questionId, text]) => <p class="omitted" key={questionId}><strong>{questionId}:</strong> {text}</p>)}
        </ReviewSection>}
        {state.error?.stage === 'semantic_validation' && <ReviewSection title="Fix the build issue" tone="warning"><p class="section-note">{state.error.hints?.join(' ')}</p><label class="question-clarification">Describe the intended correction<textarea value={state.clarifications.build_repair || ''} placeholder="For example: “the groove runs along Y, starts at Y=0, and its center is on the top surface at Z=56.”" onInput={(event) => state.setClarification('build_repair', event.currentTarget.value)} /></label><p class="section-note">Then validate the specification to replan it with DeepSeek.</p></ReviewSection>}
        {spec.features.some((feature) => feature.status === 'assumed') && <ReviewSection title="Proposed feature details">
          {spec.features.filter((feature) => feature.status === 'assumed').map((feature) => <label class="assumption" key={feature.id}><input type="checkbox" checked={state.acceptedFeatureIds.includes(feature.id)} onChange={() => state.toggleFeature(feature.id)} /> <span><strong>Use this proposed detail</strong><br />{feature.label}</span></label>)}
        </ReviewSection>}
        {spec.assumptions.length > 0 && <ReviewSection title="Proposed decisions" count={String(spec.assumptions.length)}>
          {spec.assumptions.map((assumption) => <label class="assumption" key={assumption.id}><input type="checkbox" checked={state.acceptedAssumptionIds.includes(assumption.id)} onChange={() => state.toggleAssumption(assumption.id)} /> <span><strong>Use this proposal</strong><br />{assumption.rationale}</span></label>)}
        </ReviewSection>}
        {omitted.length > 0 && <ReviewSection title="Not included in this model" count={String(omitted.length)} tone="warning"><p class="section-note">EasyCAD cannot model these features yet. They will not be included in the STL.</p>{omitted.map((feature) => <p class="omitted" key={feature.id}>{feature.label}</p>)}</ReviewSection>}
      </section>
    </div>
    {state.error && <div class="notice" role="alert"><strong>{state.error.fieldIds.length ? 'Please review the highlighted items.' : 'Build issue'}</strong><span>{state.error.message}{state.error.stage ? ` (stage: ${state.error.stage})` : ''}{state.error.requestId ? ` · reference ${state.error.requestId}` : ''}</span></div>}
    <ActionBar blockers={blockers} pending={pending} validated={state.validationPassed} onValidate={validate} onBuild={build} />
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

function DrawingPanel({ specification }: { specification: DraftSpecification }) {
  const { selectedId, setSelectedId, sourceUrl } = useAppStore()
  return <section class="drawing-panel"><header><h2>Source drawing</h2><span>Review markers</span></header><div class="drawing-stage">{sourceUrl ? <img src={sourceUrl} alt="Uploaded technical drawing" /> : <div class="drawing-empty">Drawing preview unavailable</div>}{specification.annotations.map((annotation, index) => <button type="button" class={`marker ${selectedId === annotation.field_id ? 'selected' : ''}`} style={{ left: `${annotation.x * 100}%`, top: `${annotation.y * 100}%` }} onClick={() => { setSelectedId(annotation.field_id); document.getElementById(`item-${annotation.field_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }} aria-label={`Show ${annotation.label}`} key={annotation.id}>{index + 1}</button>)}</div><footer><span><i class="legend confirmed" /> Confirmed</span><span><i class="legend proposed" /> Needs review</span><span><i class="legend conflict" /> Conflict</span></footer></section>
}

function ActionBar({ blockers, pending, validated, onValidate, onBuild }: { blockers: number; pending: boolean; validated: boolean; onValidate: () => void; onBuild: () => void }) {
  return <footer class="action-bar"><div><strong>{validated ? 'Specification validated' : `${blockers} item${blockers === 1 ? '' : 's'} to review`}</strong><span>{validated ? 'You can now build a 3D model.' : 'Enter or confirm every highlighted value before building.'}</span></div><div class="actions"><button type="button" disabled={pending} onClick={onValidate}>{pending ? 'Working…' : 'Validate specification'}</button><button type="button" class="primary" disabled={!validated || pending} onClick={onBuild}>Build 3D</button></div></footer>
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
  return <section class="preview"><div><p class="eyebrow">3D preview</p><h2>Your printable model is ready.</h2><p>Review the rendered model, then download the STL for your slicer.</p><button class="primary" type="button" onClick={() => void download()}>Download STL</button></div>{image && <img src={image} alt="Rendered CAD model" />}</section>
}

function App() {
  const specification = useAppStore((state) => state.specification)
  const error = useAppStore((state) => state.error)
  return <>{specification ? <ReviewWorkspace /> : <UploadScreen />}{!specification && error && <div class="upload-error" role="alert">{error.message}{error.stage ? ` (stage: ${error.stage})` : ''}{error.requestId ? ` · reference ${error.requestId}` : ''}</div>}</>
}

render(<App />, document.getElementById('app')!)
