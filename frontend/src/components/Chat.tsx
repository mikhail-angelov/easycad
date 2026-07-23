import { useEffect, useRef, useState } from 'preact/hooks'
import { useStore } from '../store'

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
  const setProvider = useStore((s) => s.setProvider)
  const model = useStore((s) => s.model)
  const setModel = useStore((s) => s.setModel)
  const autoRefine = useStore((s) => s.autoRefine)
  const setAutoRefine = useStore((s) => s.setAutoRefine)
  const error = useStore((s) => s.error)

  const [text, setText] = useState('')
  const proposalRef = useRef<HTMLTextAreaElement>(null)
  const logRef = useRef<HTMLDivElement>(null)

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
          <span class="chat-overlay-label">Working…</span>
        </div>
      )}
      <header>
        <h2>Chat</h2>
        <div class="chat-header-controls">
          <label class="refine-toggle" title="Refine short prompts into precise instructions">
            <input
              type="checkbox"
              checked={autoRefine}
              onChange={(e) => setAutoRefine((e.target as HTMLInputElement).checked)}
            />
            refine
          </label>
          <select value={provider} onChange={(e) => setProvider((e.target as HTMLSelectElement).value)}>
            {Object.keys(providers).map((p) => (
              <option value={p} key={p}>
                {p}
              </option>
            ))}
          </select>
          <input
            class="model-input"
            type="text"
            value={model}
            placeholder={providers[provider] ?? 'model'}
            title="Model override (blank = provider default)"
            onInput={(e) => setModel((e.target as HTMLInputElement).value)}
          />
        </div>
      </header>

      <div class="chat-log" ref={logRef}>
        {chatLog.length === 0 && !pending && (
          <p class="hint">Describe one change at a time — e.g. “add a 2 mm rim along the top edge”.</p>
        )}
        {chatLog.map((e) => (
          <div class={`chat-entry ${e.ok ? 'ok' : 'fail'}`} key={e.id}>
            <div class="bubble user">{e.prompt}</div>
            {e.refined && (
              <details class="refined">
                <summary>refined prompt</summary>
                {e.refined}
              </details>
            )}
            <div class="bubble result">{e.ok ? `Step ${e.id} ✓` : `Failed: ${e.error}`}</div>
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
            <div class="proposal-head">Refined instruction — confirm or edit:</div>
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
                Use
              </button>
              <button disabled={busy} onClick={() => dismissProposal()}>
                Cancel
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
                Generate anyway
              </button>
              <button disabled={busy} onClick={() => dismissInvalid()}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {variations && (
          <div class="variations">
            <div class="bubble user">{variations.originalPrompt}</div>
            <div class="variations-head">Pick a variation — click to preview in the viewer:</div>
            {variations.candidates.map((c, i) => (
              <button
                key={i}
                class={`variation-card ${i === selectedVariation ? 'selected' : ''} ${c.success ? '' : 'failed'}`}
                disabled={!c.success || busy}
                onClick={() => previewVariation(i)}
              >
                <span class="v-index">{i + 1}</span>
                <span class="v-info">{c.success ? summarize(c.geometry_info) : `failed: ${c.error}`}</span>
              </button>
            ))}
            <div class="variations-actions">
              <button
                class="primary"
                disabled={selectedVariation == null || busy}
                onClick={() => commitVariation()}
              >
                Use this
              </button>
              <button disabled={busy} onClick={() => cancelVariations()}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {error && <div class="chat-error">{error}</div>}

      <div class="chat-input">
        <textarea
          placeholder="Describe a change…"
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
            {busy ? '…' : 'Send'}
          </button>
          <button
            class="variations-btn"
            disabled={busy}
            title="Generate 3 variations to pick from"
            onClick={submitVariations}
          >
            ×3
          </button>
        </div>
      </div>
    </section>
  )
}
