import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from '../api'
import { useStore, useT } from '../store'
import { IconFeedback } from './Icons'

// In-app "leave feedback" button (topbar). Anonymous or signed-in; a rating and
// a contact email are optional. Local state only — no store wiring needed.
export function Feedback() {
  const t = useT()
  const authenticated = useStore((s) => s.authenticated)

  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const [rating, setRating] = useState<number | null>(null)
  const [contact, setContact] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearTimer = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }
  // Cancel any pending auto-close on unmount.
  useEffect(() => clearTimer, [])

  const close = () => {
    clearTimer()
    setOpen(false)
    setSent(false)
    setError(false)
  }

  const submit = async () => {
    const msg = message.trim()
    if (!msg || busy) return
    setBusy(true)
    setError(false)
    try {
      await api.sendFeedback(msg, rating, authenticated ? null : contact.trim() || null)
      setSent(true)
      setMessage('')
      setRating(null)
      setContact('')
      clearTimer()
      closeTimer.current = setTimeout(close, 1600)
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div class="feedback">
      <button
        class="icon-button"
        id="feedback-toggle"
        onClick={() => (open ? close() : setOpen(true))}
        title={t('feedback.tip')}
      >
        <IconFeedback />
      </button>

      {open && (
        <div class="account-panel feedback-panel">
          <div class="account-section">
            <div class="account-title">{t('feedback.title')}</div>

            {sent ? (
              <div class="account-ok">{t('feedback.thanks')}</div>
            ) : (
              <>
                <textarea
                  class="feedback-text"
                  name="feedback-message"
                  rows={4}
                  placeholder={t('feedback.placeholder')}
                  value={message}
                  onInput={(e) => setMessage((e.target as HTMLTextAreaElement).value)}
                />

                <div class="feedback-stars" role="radiogroup" aria-label={t('feedback.rateAria')}>
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button
                      type="button"
                      id={`feedback-rating-${n}`}
                      class={'star' + (rating && n <= rating ? ' on' : '')}
                      aria-label={`${n}`}
                      onClick={() => setRating(n === rating ? null : n)}
                    >
                      ★
                    </button>
                  ))}
                </div>

                {!authenticated && (
                  <input
                    type="email"
                    name="feedback-email"
                    placeholder={t('feedback.emailPlaceholder')}
                    value={contact}
                    onInput={(e) => setContact((e.target as HTMLInputElement).value)}
                  />
                )}

                {error && <div class="account-warn">{t('feedback.error')}</div>}

                <button
                  class="feedback-send"
                  id="feedback-send"
                  disabled={busy || !message.trim()}
                  onClick={submit}
                >
                  {busy ? t('feedback.sending') : t('feedback.send')}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
