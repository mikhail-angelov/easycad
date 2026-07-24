import { useStore, useT } from '../store'
import { IconGithub } from './Icons'

const REPO_URL = 'https://github.com/mikhail-angelov/easycad'

export function Timeline() {
  const steps = useStore((s) => s.steps)
  const currentId = useStore((s) => s.currentId)
  const revert = useStore((s) => s.revert)
  const busy = useStore((s) => s.busy)
  const t = useT()

  return (
    <div class="timeline">
      <span class="timeline-label">{t('timeline.steps')}</span>
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
      <span class="timeline-meta">
        <span class="timeline-copy">© 2026 Mikhail Angelov</span>
        <a
          class="timeline-gh"
          href={REPO_URL}
          target="_blank"
          rel="noopener noreferrer"
          title={t('timeline.github')}
        >
          <IconGithub size={16} />
        </a>
      </span>
    </div>
  )
}
