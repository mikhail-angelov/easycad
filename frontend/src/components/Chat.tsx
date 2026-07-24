import { useEffect, useRef, useState } from 'preact/hooks'
import { useStore, useT } from '../store'
import { STARTERS } from '../i18n'
import { Notice } from './Notice'

const WELCOME_KEY = 'easycad_welcome_seen'

export function Chat() {
  const chatLog = useStore((s) => s.chatLog)
  const sendChat = useStore((s) => s.sendChat)
  const answerClarification = useStore((s) => s.answerClarification)
  const pending = useStore((s) => s.pending)
  const proposal = useStore((s) => s.proposal)
  const confirmProposal = useStore((s) => s.confirmProposal)
  const dismissProposal = useStore((s) => s.dismissProposal)
  const invalidNotice = useStore((s) => s.invalidNotice)
  const proceedInvalid = useStore((s) => s.proceedInvalid)
  const dismissInvalid = useStore((s) => s.dismissInvalid)
  const variations = useStore((s) => s.variations)
  const selectedVariation = useStore((s) => s.selectedVariation)
  const sendVariations = useStore((s) => s.sendVariations)
  const previewVariation = useStore((s) => s.previewVariation)
  const commitVariation = useStore((s) => s.commitVariation)
  const cancelVariations = useStore((s) => s.cancelVariations)
  const busy = useStore((s) => s.busy)
  const provider = useStore((s) => s.provider)
  const providers = useStore((s) => s.providers)
  const model = useStore((s) => s.model)
  const selectModel = useStore((s) => s.selectModel)
  const hasKey = useStore((s) => s.hasKey)
  const trialTier = useStore((s) => s.trialTier)
  const trialRemaining = useStore((s) => s.trialRemaining)
  const autoRefine = useStore((s) => s.autoRefine)
  const setAutoRefine = useStore((s) => s.setAutoRefine)
  const error = useStore((s) => s.error)
  const lang = useStore((s) => s.lang)
  const t = useT()

  const models = providers[provider]?.models ?? []
  const onTrial = trialTier === 'anon' || trialTier === 'user'
  const starters = STARTERS[lang]

  const [text, setText] = useState('')
  const [showWelcome, setShowWelcome] = useState(() => {
    try {
      return localStorage.getItem(WELCOME_KEY) !== '1'
    } catch {
      return true
    }
  })
  const proposalRef = useRef<HTMLTextAreaElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Autofocus the prompt box on mount so the user can just start typing.
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const dismissWelcome = () => {
    setShowWelcome(false)
    try {
      localStorage.setItem(WELCOME_KEY, '1')
    } catch {
      /* ignore */
    }
  }

  const runStarter = (prompt: string) => {
    if (busy) return
    dismissWelcome()
    sendChat(prompt)
  }

  // Show the empty-state coaching only before any conversation/flow has started.
  const emptyState = chatLog.length === 0 && !pending && !proposal && !invalidNotice && !variations

  // Keep the latest message/prompt/proposal in view, like a normal chat.
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chatLog, pending, proposal, invalidNotice, variations, busy])

  const submit = () => {
    const t = text.trim()
    if (!t || busy) return
    setText('')
    sendChat(t)
  }

  const submitVariations = () => {
    const t = text.trim()
    if (!t || busy) return
    setText('')
    sendVariations(t)
  }

  const summarize = (info: string | null): string => {
    if (!info) return ''
    return info
      .split('\n')
      .filter((l) => l.includes('Size:') || l.includes('Topology:'))
      .map((l) => l.replace(/^#\s*/, ''))
      .join(' · ')
  }

  return (
    <section class="panel chat-panel">
      {busy && (
        <div class="chat-overlay" aria-live="polite">
          <span class="spinner" />
          <span class="chat-overlay-label">{t('chat.working')}</span>
        </div>
      )}
      <header>
        <h2>{t('chat.title')}</h2>
        <div class="chat-header-controls">
          <label class="refine-toggle" title={t('chat.refineTip')}>
            <input
              type="checkbox"
              checked={autoRefine}
              onChange={(e) => setAutoRefine((e.target as HTMLInputElement).checked)}
            />
            {t('chat.refine')}
          </label>
          {/* On trial the model is fixed (operator DeepSeek key); the picker is a
              BYOK-only live control. On trial we show the remaining free count. */}
          {hasKey ? (
            <select
              class="model-select"
              value={model}
              title={t('chat.modelTip', { provider })}
              disabled={busy}
              onChange={(e) => selectModel((e.target as HTMLSelectElement).value)}
            >
              {models.map((mo) => (
                <option value={mo} key={mo}>
                  {mo}
                </option>
              ))}
            </select>
          ) : (
            onTrial &&
            trialRemaining != null && (
              <span
                class={`trial-pill ${trialRemaining <= 0 ? 'empty' : ''}`}
                title={trialTier === 'anon' ? t('chat.trialAnonTip') : t('chat.trialUserTip')}
              >
                {trialTier === 'anon' && trialRemaining > 0
                  ? t('chat.trialNoSignup', { n: trialRemaining })
                  : t('chat.trialLeft', { n: trialRemaining })}
              </span>
            )
          )}
        </div>
      </header>

      <div class="chat-log" ref={logRef}>
        {emptyState && (
          <div class="empty-state">
            {showWelcome && (
              <div class="welcome">
                <button class="welcome-dismiss" title={t('chat.dismiss')} onClick={dismissWelcome}>
                  ×
                </button>
                <div class="welcome-title">{t('chat.welcomeTitle')}</div>
                <div class="welcome-body">{t('chat.welcomeBody')}</div>
              </div>
            )}
            <p class="hint">{t('chat.emptyHint')}</p>
            <div class="starter-chips">
              {starters.map((p) => (
                <button key={p} class="starter-chip" disabled={busy} onClick={() => runStarter(p)}>
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}
        {chatLog.map((e) => (
          <div class={`chat-entry ${e.ok ? 'ok' : 'fail'}`} key={e.id}>
            <div class="bubble user">{e.prompt}</div>
            {e.refined && (
              <details class="refined">
                <summary>{t('chat.refinedPrompt')}</summary>
                {e.refined}
              </details>
            )}
            <div class="bubble result">
              {e.ok ? t('chat.stepOk', { id: e.id }) : t('chat.failed', { error: e.error ?? '' })}
            </div>
          </div>
        ))}

        {pending && (
          <div class="clarify">
            <div class="bubble user">{pending.originalPrompt}</div>
            {pending.questions.map((q, qi) => (
              <div class="clarify-q" key={qi}>
                <div class="clarify-question">{q.question}</div>
                <div class="clarify-options">
                  {q.options.map((opt, oi) => (
                    <button
                      key={oi}
                      class="clarify-option"
                      disabled={busy}
                      onClick={() => answerClarification(opt)}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {proposal && (
          <div class="proposal">
            <div class="bubble user">{proposal.originalPrompt}</div>
            <div class="proposal-head">{t('chat.proposalHead')}</div>
            <textarea
              key={proposal.originalPrompt}
              ref={proposalRef}
              class="proposal-text"
              disabled={busy}
              defaultValue={proposal.refinedPrompt}
            />
            <div class="proposal-actions">
              <button
                class="primary"
                disabled={busy}
                onClick={() => confirmProposal(proposalRef.current?.value)}
              >
                {t('chat.use')}
              </button>
              <button disabled={busy} onClick={() => dismissProposal()}>
                {t('chat.cancel')}
              </button>
            </div>
          </div>
        )}

        {invalidNotice && (
          <div class="invalid">
            <div class="bubble user">{invalidNotice.originalPrompt}</div>
            <div class="invalid-reason">{invalidNotice.reason}</div>
            <div class="invalid-actions">
              <button disabled={busy} onClick={() => proceedInvalid()}>
                {t('chat.generateAnyway')}
              </button>
              <button disabled={busy} onClick={() => dismissInvalid()}>
                {t('chat.cancel')}
              </button>
            </div>
          </div>
        )}

        {variations && (
          <div class="variations">
            <div class="bubble user">{variations.originalPrompt}</div>
            <div class="variations-head">{t('chat.variationsHead')}</div>
            {variations.candidates.map((c, i) => (
              <button
                key={i}
                class={`variation-card ${i === selectedVariation ? 'selected' : ''} ${c.success ? '' : 'failed'}`}
                disabled={!c.success || busy}
                onClick={() => previewVariation(i)}
              >
                <span class="v-index">{i + 1}</span>
                <span class="v-info">
                  {c.success ? summarize(c.geometry_info) : t('chat.variationFailed', { error: c.error ?? '' })}
                </span>
              </button>
            ))}
            <div class="variations-actions">
              <button
                class="primary"
                disabled={selectedVariation == null || busy}
                onClick={() => commitVariation()}
              >
                {t('chat.useThis')}
              </button>
              <button disabled={busy} onClick={() => cancelVariations()}>
                {t('chat.cancel')}
              </button>
            </div>
          </div>
        )}
      </div>

      <Notice />
      {error && <div class="chat-error">{error}</div>}

      <div class="chat-input">
        <textarea
          ref={inputRef}
          placeholder={t('chat.inputPlaceholder')}
          value={text}
          disabled={busy}
          onInput={(e) => setText((e.target as HTMLTextAreaElement).value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <div class="chat-send">
          <button class="primary" disabled={busy} onClick={submit}>
            {busy ? '…' : t('chat.send')}
          </button>
          <button
            class="variations-btn"
            disabled={busy}
            title={t('chat.variationsTip')}
            onClick={submitVariations}
          >
            ×3
          </button>
        </div>
      </div>
    </section>
  )
}
