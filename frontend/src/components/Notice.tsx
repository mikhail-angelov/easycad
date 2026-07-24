import { useStore } from '../store'

// Orange warning banner (SPEC14) — distinct from the red generation error.
// Driven by the store's `notice`, set from coded API errors (trial exhaustion).
export function Notice() {
  const notice = useStore((s) => s.notice)
  const dismiss = useStore((s) => s.dismissNotice)
  const openAccount = useStore((s) => s.setAccountOpen)

  if (!notice) return null

  const isAnon = notice.code === 'trial_exhausted_anon'
  const openKeyForm = () => {
    dismiss()
    openAccount(true)
  }

  return (
    <div class="notice" role="status">
      <span class="notice-msg">{notice.message}</span>
      <span class="notice-actions">
        {isAnon && (
          <button class="notice-cta" onClick={openKeyForm}>
            Sign in
          </button>
        )}
        <button class="notice-cta" onClick={openKeyForm}>
          Add your key
        </button>
        <button class="notice-dismiss" title="Dismiss" onClick={() => dismiss()}>
          ×
        </button>
      </span>
    </div>
  )
}
