import { useStore, useT } from '../store'
import type { Lang } from '../i18n'

// Compact EN/RU switch in the topbar. Persists via the store (localStorage),
// shared with the landing page's language choice.
export function LangToggle() {
  const lang = useStore((s) => s.lang)
  const setLang = useStore((s) => s.setLang)
  const t = useT()
  const langs: Lang[] = ['en', 'ru']

  return (
    <div class="lang-toggle" role="group" aria-label={t('lang.ariaLabel')}>
      {langs.map((l) => (
        <button
          key={l}
          class={`lang-opt ${lang === l ? 'active' : ''}`}
          aria-pressed={lang === l}
          onClick={() => setLang(l)}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
