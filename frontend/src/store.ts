import { create } from 'zustand'
import {
  ApiError,
  api,
  type Candidate,
  type ClarifyQuestion,
  type ProviderInfo,
  type SessionPayload,
  type Step,
  type TrialTier,
  type ValidateKeyResult,
} from './api'
import { LANG_KEY, type Lang, detectLang, translate } from './i18n'
import { track } from './analytics'
import { SOFT_CODES, TRIAL_CODES, preservesPrompt } from './notices'

// Orange warning banner (SPEC14), distinct from the red `error`.
export interface Notice {
  message: string
  code: string | null
}


export interface ChatEntry {
  id: number
  prompt: string
  refined: string | null
  ok: boolean
  error: string | null
}

export interface Pending {
  originalPrompt: string
  questions: ClarifyQuestion[]
}

export interface Proposal {
  originalPrompt: string
  refinedPrompt: string
}

export interface InvalidNotice {
  originalPrompt: string
  reason: string
}

export interface Variations {
  candidates: Candidate[]
  originalPrompt: string
  refined: string | null
}

// Rebuild the chat history from stored steps (used on load/resume).
function chatLogFromSteps(steps: Step[]): ChatEntry[] {
  return steps
    .filter((s) => s.original_prompt)
    .map((s) => ({
      id: s.id,
      prompt: s.original_prompt as string,
      refined: s.refined_prompt,
      ok: s.success,
      error: s.error,
    }))
}

interface State {
  steps: Step[]
  currentId: number | null
  code: string // editor contents
  stlBase64: string | null // model currently shown in the viewer
  geometryInfo: string | null
  providers: Record<string, ProviderInfo>
  provider: string
  model: string
  lang: Lang
  trialTier: TrialTier | null
  trialRemaining: number | null
  autoRefine: boolean
  chatLog: ChatEntry[]
  pending: Pending | null
  proposal: Proposal | null
  invalidNotice: InvalidNotice | null
  variations: Variations | null
  selectedVariation: number | null
  busy: boolean
  busyKind: 'gen' | null // 'gen' = an LLM generation is running (staged progress)
  error: string | null
  notice: Notice | null
  // A prompt to restore into the chat box after a retryable failure (server_busy),
  // so the user can re-send with one click. Consumed (cleared) by the input.
  retryPrompt: string | null
  accountOpen: boolean
  authenticated: boolean
  email: string | null
  hasKey: boolean
  authMessage: string | null

  init: () => Promise<void>
  login: (email: string) => Promise<void>
  logout: () => Promise<void>
  validateKey: (provider: string, key: string) => Promise<ValidateKeyResult>
  saveKey: (provider: string, model: string, key: string) => Promise<void>
  removeKey: () => Promise<void>
  deleteAccount: () => Promise<void>
  setCode: (code: string) => void
  selectModel: (model: string) => Promise<void>
  setLang: (lang: Lang) => void
  dismissNotice: () => void
  clearRetryPrompt: () => void
  setAccountOpen: (open: boolean) => void
  setAutoRefine: (on: boolean) => void
  sendChat: (prompt: string) => Promise<void>
  answerClarification: (answer: string) => Promise<void>
  confirmProposal: (editedText?: string) => Promise<void>
  dismissProposal: () => void
  proceedInvalid: () => Promise<void>
  dismissInvalid: () => void
  sendVariations: (prompt: string) => Promise<void>
  previewVariation: (index: number) => void
  commitVariation: () => Promise<void>
  cancelVariations: () => Promise<void>
  runManual: () => Promise<void>
  revert: (stepId: number) => Promise<void>
  reset: () => Promise<void>
  importProject: (text: string) => Promise<void>
}

export const useStore = create<State>((set, get) => {
  // Fire `trial_exhausted` at most once per page load, from whichever path sees
  // it first: the successful last-trial generation that drives remaining → 0
  // (`depleted`), or a later rejected request (`rejected`) as a fallback. The
  // flag dedupes so the two don't double-count.
  let trialExhaustedFired = false
  function fireTrialExhausted(via: 'depleted' | 'rejected') {
    if (trialExhaustedFired) return
    trialExhaustedFired = true
    track('trial_exhausted', { via })
  }

  // Sync editor + viewer to a session's current step.
  function applySession(session: SessionPayload) {
    const cur = session.current
    set({
      steps: session.steps,
      currentId: session.current_id,
      providers: session.providers,
      code: cur?.code ?? get().code,
      stlBase64: cur?.stl_base64 ?? null,
      geometryInfo: cur?.geometry_info ?? null,
    })
    applyTrial(session)
  }

  // Keep the trial tier/remaining fresh, and detect the >0 → 0 transition (the
  // moment a generation uses up the last free run). Guarded on a numeric prior
  // so a fresh load of an already-exhausted user (null → 0) doesn't count.
  function setTrial(tier: TrialTier | null, remaining: number | null) {
    const prev = get().trialRemaining
    if (typeof prev === 'number' && prev > 0 && remaining === 0) fireTrialExhausted('depleted')
    set({ trialTier: tier, trialRemaining: remaining })
  }

  function applyTrial(session: SessionPayload) {
    setTrial(session.settings.trial_tier ?? null, session.settings.trial_remaining ?? null)
  }

  // Map a thrown API error to either the orange trial notice or the red error.
  // Generation call sites pass `genSource` so a network/provider failure (a
  // thrown error, not a returned unsuccessful step) still counts as a health
  // signal; non-generation callers omit it.
  function reportError(e: unknown, genSource?: string, retryPrompt?: string) {
    if (e instanceof ApiError && e.code && TRIAL_CODES.has(e.code)) {
      fireTrialExhausted('rejected')
      set({ notice: { message: e.message, code: e.code } })
    } else if (e instanceof ApiError && e.code && SOFT_CODES.has(e.code)) {
      // Operational: orange notice, not a red error. `server_busy` preserves the
      // prompt so the user can retry in one click without retyping.
      if (genSource) track('generation_failed', { source: genSource })
      set({
        notice: { message: e.message, code: e.code },
        ...(preservesPrompt(e.code) && retryPrompt ? { retryPrompt } : {}),
      })
    } else {
      if (genSource) track('generation_failed', { source: genSource })
      set({ error: e instanceof Error ? e.message : String(e) })
    }
  }

  // Core chat round-trip shared by send / confirm / proceed-anyway.
  async function doChat(prompt: string, autoRefine: boolean, refinedOverride?: string) {
    const { code, provider, model, lang } = get()
    set({ busy: true, busyKind: 'gen', error: null, notice: null })
    try {
      const res = await api.chat(prompt, code, provider, model || undefined, autoRefine, refinedOverride, lang)
      set({ steps: res.session.steps, currentId: res.session.current_id })
      applyTrial(res.session)

      if (res.action === 'clarify') {
        track('clarify_verdict', { source: 'chat' })
        set({ pending: { originalPrompt: prompt, questions: res.questions } })
        return
      }
      if (res.action === 'confirm_refine') {
        set({ proposal: { originalPrompt: prompt, refinedPrompt: res.refined_prompt ?? '' } })
        return
      }
      if (res.action === 'invalid') {
        track('invalid_verdict', { source: 'chat' })
        set({ invalidNotice: { originalPrompt: prompt, reason: res.reason ?? translate(get().lang, 'chat.inconsistent') } })
        return
      }

      // action === 'generated'
      const step = res.step!
      set({
        chatLog: [
          ...get().chatLog,
          { id: step.id, prompt, refined: res.refined_prompt, ok: step.success, error: step.error },
        ],
      })
      if (step.success) {
        track('step_success', { source: 'chat' })
        set({ code: step.code, stlBase64: step.stl_base64, geometryInfo: step.geometry_info, error: null })
      } else {
        track('generation_failed', { source: 'chat' })
        set({ code: step.code, error: step.error })
      }
    } catch (e) {
      reportError(e, 'chat', prompt)
    } finally {
      set({ busy: false, busyKind: null })
    }
  }

  return {
    steps: [],
    currentId: null,
    code: '',
    stlBase64: null,
    geometryInfo: null,
    providers: {},
    provider: 'deepseek',
    model: '',
    lang: detectLang(),
    trialTier: null,
    trialRemaining: null,
    autoRefine: true,
    chatLog: [],
    pending: null,
    proposal: null,
    invalidNotice: null,
    variations: null,
    selectedVariation: null,
    busy: false,
    busyKind: null,
    error: null,
    notice: null,
    retryPrompt: null,
    accountOpen: false,
    authenticated: false,
    email: null,
    hasKey: false,
    authMessage: null,

    async init() {
      set({ busy: true, error: null })
      try {
        const session = await api.session()
        applySession(session)
        track('app_open', { tier: session.settings.trial_tier ?? 'unknown' })
        set({
          provider: session.settings.provider || session.default_provider,
          model: session.settings.model ?? '',
          authenticated: session.auth.authenticated,
          email: session.auth.email,
          hasKey: session.settings.has_key,
          chatLog: chatLogFromSteps(session.steps),
        })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    async login(email) {
      set({ busy: true, error: null, authMessage: null })
      try {
        await api.login(email)
        // Store the address only; the component localizes the confirmation text.
        set({ authMessage: email })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    async logout() {
      set({ busy: true, error: null })
      try {
        await api.logout()
        const me = await api.me()
        set({
          authenticated: false,
          email: null,
          hasKey: me.settings.has_key,
          trialTier: me.settings.trial_tier ?? null,
          trialRemaining: me.settings.trial_remaining ?? null,
          authMessage: null,
        })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    validateKey(provider, key) {
      return api.validateKey(provider, key)
    },

    async saveKey(provider, model, key) {
      set({ busy: true, error: null })
      try {
        const s = await api.saveSettings({ provider, model: model || undefined, key })
        set({
          hasKey: s.has_key,
          provider: s.provider,
          model: s.model ?? '',
          trialTier: s.trial_tier ?? null,
          trialRemaining: s.trial_remaining ?? null,
        })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    async removeKey() {
      set({ busy: true, error: null })
      try {
        // Empty key clears it server-side (has_key ⇒ false); provider/model stay.
        const s = await api.saveSettings({ key: '' })
        set({
          hasKey: s.has_key,
          trialTier: s.trial_tier ?? null,
          trialRemaining: s.trial_remaining ?? null,
        })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    async deleteAccount() {
      set({ busy: true, error: null })
      try {
        await api.deleteAccount()
        set({ authenticated: false, email: null, hasKey: false, authMessage: null })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    setCode: (code) => set({ code }),

    // Live model switch for a BYOK key: persist so generation actually uses it
    // (the backend reads the saved model for BYOK calls).
    async selectModel(model) {
      set({ model })
      if (!get().hasKey) return
      try {
        await api.saveSettings({ provider: get().provider, model })
      } catch (e) {
        reportError(e)
      }
    },

    setLang: (lang) => {
      try {
        localStorage.setItem(LANG_KEY, lang)
      } catch {
        /* ignore */
      }
      set({ lang })
    },

    dismissNotice: () => set({ notice: null }),
    clearRetryPrompt: () => set({ retryPrompt: null }),
    setAccountOpen: (accountOpen) => set({ accountOpen }),
    setAutoRefine: (autoRefine) => set({ autoRefine }),

    async sendChat(prompt) {
      track('prompt_sent', { mode: 'chat' })
      set({ pending: null, proposal: null, invalidNotice: null })
      await doChat(prompt, get().autoRefine)
    },

    async answerClarification(answer) {
      const p = get().pending
      if (!p) return
      set({ pending: null })
      await doChat(`${p.originalPrompt} — ${answer}`, get().autoRefine)
    },

    async confirmProposal(editedText) {
      const p = get().proposal
      if (!p) return
      set({ proposal: null })
      // Confirmed refinement → generate directly from it (no re-triage). The
      // skills triage chose are held server-side and applied on this turn (SPEC15).
      await doChat(p.originalPrompt, false, editedText ?? p.refinedPrompt)
    },

    dismissProposal: () => set({ proposal: null }),

    async proceedInvalid() {
      const n = get().invalidNotice
      if (!n) return
      set({ invalidNotice: null })
      // Generate the original request directly, bypassing triage.
      await doChat(n.originalPrompt, false)
    },

    dismissInvalid: () => set({ invalidNotice: null }),

    async sendVariations(prompt) {
      const { code, provider, model, autoRefine, lang } = get()
      track('prompt_sent', { mode: 'variations' })
      set({ busy: true, busyKind: 'gen', error: null, notice: null, pending: null, proposal: null, invalidNotice: null, variations: null, selectedVariation: null })
      try {
        const res = await api.variations(prompt, code, provider, model || undefined, autoRefine, 3, lang)
        if (res.action === 'clarify') {
          track('clarify_verdict', { source: 'variations' })
          set({ pending: { originalPrompt: prompt, questions: res.questions } })
          return
        }
        if (res.action === 'invalid') {
          track('invalid_verdict', { source: 'variations' })
          set({ invalidNotice: { originalPrompt: prompt, reason: res.reason ?? translate(get().lang, 'chat.inconsistent') } })
          return
        }
        // Apply the server's post-charge trial status (no session payload here);
        // the client does not re-derive the "charge once" rule (SPEC14).
        if (res.trial_tier !== undefined) {
          setTrial(res.trial_tier, res.trial_remaining ?? null)
        }
        if (!res.candidates.some((c) => c.success)) {
          track('generation_failed', { source: 'variations' })
        }
        set({
          variations: { candidates: res.candidates, originalPrompt: prompt, refined: res.refined_prompt },
        })
      } catch (e) {
        reportError(e, 'variations', prompt)
      } finally {
        set({ busy: false, busyKind: null })
      }
    },

    previewVariation(index) {
      const v = get().variations
      const c = v?.candidates[index]
      if (!c || !c.success || !c.code) return
      set({
        selectedVariation: index,
        code: c.code,
        stlBase64: c.stl_base64,
        geometryInfo: c.geometry_info,
      })
    },

    async commitVariation() {
      const v = get().variations
      const i = get().selectedVariation
      if (!v || i == null) return
      const c = v.candidates[i]
      if (!c.success || !c.code) return
      set({ busy: true, error: null })
      try {
        const { step, session } = await api.commit(c.code, v.originalPrompt, v.refined)
        set({
          steps: session.steps,
          currentId: session.current_id,
          chatLog: [
            ...get().chatLog,
            { id: step.id, prompt: v.originalPrompt, refined: v.refined, ok: step.success, error: step.error },
          ],
          variations: null,
          selectedVariation: null,
        })
        if (step.success) {
          track('step_success', { source: 'variation' })
          set({ code: step.code, stlBase64: step.stl_base64, geometryInfo: step.geometry_info })
        }
      } catch (e) {
        reportError(e, 'variation')
      } finally {
        set({ busy: false })
      }
    },

    async cancelVariations() {
      const cur = get().currentId
      set({ variations: null, selectedVariation: null })
      // A preview may have overwritten the editor/viewer — restore current step.
      if (cur != null) await get().revert(cur)
    },

    async runManual() {
      const { code } = get()
      set({ busy: true, error: null })
      try {
        const { step, session } = await api.executeManual(code)
        set({ steps: session.steps, currentId: session.current_id })
        if (step.success) {
          track('step_success', { source: 'manual' })
          set({ stlBase64: step.stl_base64, geometryInfo: step.geometry_info, code: step.code, error: null })
        } else {
          track('generation_failed', { source: 'manual' })
          set({ error: step.error })
        }
      } catch (e) {
        reportError(e, 'manual')
      } finally {
        set({ busy: false })
      }
    },

    async revert(stepId) {
      set({ busy: true, error: null })
      try {
        applySession(await api.revert(stepId))
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    async reset() {
      set({ busy: true, error: null })
      try {
        applySession(await api.reset())
        set({ chatLog: [], pending: null, proposal: null, invalidNotice: null, variations: null, selectedVariation: null })
      } catch (e) {
        reportError(e)
      } finally {
        set({ busy: false })
      }
    },

    async importProject(text) {
      set({ busy: true, error: null })
      try {
        const project = JSON.parse(text)
        const session = await api.importProject(project)
        applySession(session)
        set({
          chatLog: chatLogFromSteps(session.steps),
          pending: null,
          proposal: null,
          invalidNotice: null,
          variations: null,
          selectedVariation: null,
        })
      } catch (e) {
        set({ error: translate(get().lang, 'store.loadProjectError', { error: String(e) }) })
      } finally {
        set({ busy: false })
      }
    },
  }
})

// Translation hook bound to the current language — components call `const t =
// useT()` then `t('some.key', { param })`. Re-renders on language change.
export function useT() {
  const lang = useStore((s) => s.lang)
  return (key: string, params?: Record<string, string | number>) => translate(lang, key, params)
}
