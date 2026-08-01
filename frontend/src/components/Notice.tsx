import { useStore, useT } from '../store'
import { LOCALIZED_CODES, TRIAL_CODES } from '../notices'

// Orange warning banner (SPEC14) — distinct from the red generation error.
// Driven by the store's `notice`, set from coded API errors (trial exhaustion).
export function Notice() {
  const notice = useStore((s) => s.notice)
  const dismiss = useStore((s) => s.dismissNotice)
  const openAccount = useStore((s) => s.setAccountOpen)
  const t = useT()

  if (!notice) return null

  const isAnon = notice.code === 'trial_exhausted_anon'
  const isTrial = !!notice.code && TRIAL_CODES.has(notice.code)
  // Server messages are English; localize known codes, else show as-is.
  const message = notice.code && LOCALIZED_CODES.has(notice.code) ? t(`notice.${notice.code}`) : notice.message
  const openKeyForm = () => {
    dismiss()
    openAccount(true)
  }

  return (
    <div class="notice" role="status">
      <span class="notice-msg">{message}</span>
      <span class="notice-actions">
        {isTrial && isAnon && (
          <button class="notice-cta" onClick={openKeyForm}>
            {t('notice.signIn')}
          </button>
        )}
        {isTrial && (
          <button class="notice-cta" onClick={openKeyForm}>
            {t('notice.addKey')}
          </button>
        )}
        <button class="notice-dismiss" title={t('notice.dismiss')} onClick={() => dismiss()}>
          ×
        </button>
      </span>
    </div>
  )
}
