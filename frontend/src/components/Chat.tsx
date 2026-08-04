import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import { useStore, useT } from '../store'
import { STARTERS } from '../i18n'
import { Notice } from './Notice'
import { formatGeometryInfo } from '../geometry'

const WELCOME_KEY = 'easycad_welcome_seen'
const MAX_TEXTAREA_HEIGHT = 180

function resizeTextarea(el: HTMLTextAreaElement | null) {
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`
}

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
  const busyKind = useStore((s) => s.busyKind)
  const retryPrompt = useStore((s) => s.retryPrompt)
  const clearRetryPrompt = useStore((s) => s.clearRetryPrompt)
  const setAccountOpen = useStore((s) => s.setAccountOpen)
  const t = useT()

  // Staged progress for LLM generations: since the server does triage → generate
  // → execute inside one request, advance a plausible label by elapsed time so a
  // long wait doesn't read as frozen. Non-generation waits keep a generic label.
  const [genStage, setGenStage] = useState(0)
  const [genElapsedSeconds, setGenElapsedSeconds] = useState(0)
  useEffect(() => {
    if (!busy || busyKind !== 'gen') {
      setGenStage(0)
      setGenElapsedSeconds(0)
      return
    }
    const start = Date.now()
    const id = setInterval(() => {
      const elapsed = (Date.now() - start) / 1000
      setGenStage(elapsed < 2.5 ? 0 : elapsed < 8 ? 1 : 2)
      setGenElapsedSeconds(Math.floor(elapsed))
    }, 400)
    return () => clearInterval(id)
  }, [busy, busyKind])

  const overlayLabel =
    busyKind === 'gen'
      ? `${[t('chat.stageThinking'), t('chat.stageGenerating'), t('chat.stageBuilding')][genStage]} · ${genElapsedSeconds} s`
      : t('chat.working')

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

  // After a retryable failure (server_busy), the store hands back the prompt that
  // was cleared on submit so the user can re-send in one click without retyping.
  useEffect(() => {
    if (retryPrompt != null) {
      setText(retryPrompt)
      clearRetryPrompt()
      inputRef.current?.focus()
    }
  }, [retryPrompt, clearRetryPrompt])

  // Reflow after every state change too: retry restores and send clears do not
  // originate from an input event.
  useLayoutEffect(() => {
    resizeTextarea(inputRef.current)
  }, [text])

  useLayoutEffect(() => {
    resizeTextarea(proposalRef.current)
  }, [proposal])

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

  return (
    <section class="panel chat-panel">
      {busy && (
        <div class="chat-overlay" aria-live="polite">
          <span class="spinner" />
          <span class="chat-overlay-label">{overlayLabel}</span>
        </div>
      )}
      <header>
        <h2>{t('chat.title')}</h2>
        <div class="chat-header-controls">
          <label class="refine-toggle" title={t('chat.refineTip')}>
            <input
              type="checkbox"
              name="auto-refine"
              data-testid="auto-refine"
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
              name="model"
              data-testid="model-select"
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
            trialRemaining != null && (trialRemaining <= 0 ? (
              <button
                type="button"
                id="trial-cta"
                data-testid="trial-cta"
                class={`trial-pill ${trialRemaining <= 0 ? 'empty' : ''}`}
                title={trialTier === 'anon' ? t('chat.trialAnonTip') : t('chat.trialUserTip')}
                onClick={() => setAccountOpen(true)}
              >
                {t('chat.trialLeft', { n: trialRemaining })}
              </button>
            ) : (
              <span class="trial-pill" title={trialTier === 'anon' ? t('chat.trialAnonTip') : t('chat.trialUserTip')}>
                {trialTier === 'anon' ? t('chat.trialNoSignup', { n: trialRemaining }) : t('chat.trialLeft', { n: trialRemaining })}
              </span>
            ))
          )}
        </div>
      </header>

      <div class="chat-log" ref={logRef}>
        {emptyState && (
          <div class="empty-state">
            {showWelcome && (
              <div class="welcome">
                <button id="welcome-dismiss" class="welcome-dismiss" title={t('chat.dismiss')} onClick={dismissWelcome}>
                  ×
                </button>
                <div class="welcome-title">{t('chat.welcomeTitle')}</div>
                <div class="welcome-body">{t('chat.welcomeBody')}</div>
              </div>
            )}
            <p class="hint">{t('chat.emptyHint')}</p>
            <div class="starter-chips">
              {starters.map((p, i) => (
                <button id={`starter-${i}`} key={p} class="starter-chip" disabled={busy} onClick={() => runStarter(p)}>
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
                <summary id={`refined-prompt-${e.id}`}>{t('chat.refinedPrompt')}</summary>
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
                      id={`clarify-${qi}-${oi}`}
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
              name="refined-prompt"
              data-testid="refined-prompt"
              class="proposal-text"
              disabled={busy}
              defaultValue={proposal.refinedPrompt}
              onInput={(e) => resizeTextarea(e.currentTarget)}
            />
            <div class="proposal-actions">
              <button
                class="primary"
                id="proposal-use"
                disabled={busy}
                onClick={() => confirmProposal(proposalRef.current?.value)}
              >
                {t('chat.use')}
              </button>
              <button id="proposal-cancel" disabled={busy} onClick={() => dismissProposal()}>
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
              <button id="invalid-generate" disabled={busy} onClick={() => proceedInvalid()}>
                {t('chat.generateAnyway')}
              </button>
              <button id="invalid-cancel" disabled={busy} onClick={() => dismissInvalid()}>
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
                id={`variation-option-${i}`}
                class={`variation-card ${i === selectedVariation ? 'selected' : ''} ${c.success ? '' : 'failed'}`}
                disabled={!c.success || busy}
                onClick={() => previewVariation(i)}
              >
                <span class="v-index">{i + 1}</span>
                <span class="v-info">
                  {c.success ? formatGeometryInfo(c.geometry_info, t) : t('chat.variationFailed', { error: c.error ?? '' })}
                </span>
              </button>
            ))}
            <div class="variations-actions">
              <button
                class="primary"
                id="variation-commit"
                disabled={selectedVariation == null || busy}
                onClick={() => commitVariation()}
              >
                {t('chat.useThis')}
              </button>
              <button id="variation-cancel" disabled={busy} onClick={() => cancelVariations()}>
                {t('chat.cancel')}
              </button>
            </div>
          </div>
        )}
      </div>

      <Notice />
      {error && <div class="chat-error">{error}</div>}

      <div class="chat-input">
        <div class="chat-compose">
          <textarea
            ref={inputRef}
            id="chat-prompt"
            name="chat-prompt"
            data-testid="chat-prompt"
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
          <div class="chat-input-hint">{t('chat.inputHint')}</div>
        </div>
        <div class="chat-send">
          <button id="chat-send" data-testid="chat-send" class="primary" disabled={busy} onClick={submit}>
            {busy ? '…' : t('chat.send')}
          </button>
          <button
            class="variations-btn"
            id="chat-variations"
            data-testid="chat-variations"
            disabled={busy}
            title={t('chat.variationsTip')}
            onClick={submitVariations}
          >
            {t('chat.variations')}
          </button>
        </div>
      </div>
    </section>
  )
}
