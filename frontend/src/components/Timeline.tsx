import { useStore } from '../store'

export function Timeline() {
  const steps = useStore((s) => s.steps)
  const currentId = useStore((s) => s.currentId)
  const revert = useStore((s) => s.revert)
  const busy = useStore((s) => s.busy)

  return (
    <div class="timeline">
      <span class="timeline-label">Steps</span>
      {steps.map((s) => (
        <button
          key={s.id}
          disabled={busy}
          class={`node ${s.id === currentId ? 'current' : ''} ${s.success ? '' : 'failed'}`}
          title={s.original_prompt ?? s.kind}
          onClick={() => revert(s.id)}
        >
          {s.id}
        </button>
      ))}
    </div>
  )
}
