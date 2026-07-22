import { useState } from 'preact/hooks'
import { useStore } from '../store'

export function Chat() {
  const chatLog = useStore((s) => s.chatLog)
  const sendChat = useStore((s) => s.sendChat)
  const busy = useStore((s) => s.busy)
  const provider = useStore((s) => s.provider)
  const providers = useStore((s) => s.providers)
  const setProvider = useStore((s) => s.setProvider)
  const error = useStore((s) => s.error)

  const [text, setText] = useState('')

  const submit = () => {
    const t = text.trim()
    if (!t || busy) return
    setText('')
    sendChat(t)
  }

  return (
    <section class="panel chat-panel">
      <header>
        <h2>Chat</h2>
        <select value={provider} onChange={(e) => setProvider((e.target as HTMLSelectElement).value)}>
          {Object.keys(providers).map((p) => (
            <option value={p} key={p}>
              {p}
            </option>
          ))}
        </select>
      </header>

      <div class="chat-log">
        {chatLog.length === 0 && <p class="hint">Describe one change at a time — e.g. “make an open-top box, 2mm walls”.</p>}
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
        <button class="primary" disabled={busy} onClick={submit}>
          {busy ? '…' : 'Send'}
        </button>
      </div>
    </section>
  )
}
