import { create } from 'zustand'
import { api, type Candidate, type ClarifyQuestion, type SessionPayload, type Step } from './api'

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
  providers: Record<string, string>
  provider: string
  model: string
  autoRefine: boolean
  chatLog: ChatEntry[]
  pending: Pending | null
  proposal: Proposal | null
  invalidNotice: InvalidNotice | null
  variations: Variations | null
  selectedVariation: number | null
  busy: boolean
  error: string | null
  authenticated: boolean
  email: string | null
  hasKey: boolean
  authMessage: string | null

  init: () => Promise<void>
  login: (email: string) => Promise<void>
  logout: () => Promise<void>
  saveKey: (key: string) => Promise<void>
  deleteAccount: () => Promise<void>
  setCode: (code: string) => void
  setProvider: (provider: string) => void
  setModel: (model: string) => void
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
  }

  // Core chat round-trip shared by send / confirm / proceed-anyway.
  async function doChat(prompt: string, autoRefine: boolean, refinedOverride?: string) {
    const { code, provider, model } = get()
    set({ busy: true, error: null })
    try {
      const res = await api.chat(prompt, code, provider, model || undefined, autoRefine, refinedOverride)
      set({ steps: res.session.steps, currentId: res.session.current_id })

      if (res.action === 'clarify') {
        set({ pending: { originalPrompt: prompt, questions: res.questions } })
        return
      }
      if (res.action === 'confirm_refine') {
        set({ proposal: { originalPrompt: prompt, refinedPrompt: res.refined_prompt ?? '' } })
        return
      }
      if (res.action === 'invalid') {
        set({ invalidNotice: { originalPrompt: prompt, reason: res.reason ?? 'Inconsistent request.' } })
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
        set({ code: step.code, stlBase64: step.stl_base64, geometryInfo: step.geometry_info, error: null })
      } else {
        set({ code: step.code, error: step.error })
      }
    } catch (e) {
      set({ error: String(e) })
    } finally {
      set({ busy: false })
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
    autoRefine: true,
    chatLog: [],
    pending: null,
    proposal: null,
    invalidNotice: null,
    variations: null,
    selectedVariation: null,
    busy: false,
    error: null,
    authenticated: false,
    email: null,
    hasKey: false,
    authMessage: null,

    async init() {
      set({ busy: true, error: null })
      try {
        const session = await api.session()
        applySession(session)
        set({
          provider: session.settings.provider || session.default_provider,
          model: session.settings.model ?? '',
          authenticated: session.auth.authenticated,
          email: session.auth.email,
          hasKey: session.settings.has_key,
          chatLog: chatLogFromSteps(session.steps),
        })
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },

    async login(email) {
      set({ busy: true, error: null, authMessage: null })
      try {
        await api.login(email)
        set({ authMessage: 'We sent a sign-in link to ' + email })
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },

    async logout() {
      set({ busy: true, error: null })
      try {
        await api.logout()
        const me = await api.me()
        set({ authenticated: false, email: null, hasKey: me.settings.has_key, authMessage: null })
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },

    async saveKey(key) {
      const { provider, model } = get()
      set({ busy: true, error: null })
      try {
        const s = await api.saveSettings({ provider, model: model || undefined, key })
        set({ hasKey: s.has_key })
      } catch (e) {
        set({ error: String(e) })
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
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },

    setCode: (code) => set({ code }),
    setProvider: (provider) => set({ provider, model: '' }),
    setModel: (model) => set({ model }),
    setAutoRefine: (autoRefine) => set({ autoRefine }),

    async sendChat(prompt) {
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
      // Confirmed refinement → generate directly from it (no re-triage).
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
      const { code, provider, model, autoRefine } = get()
      set({ busy: true, error: null, pending: null, proposal: null, invalidNotice: null, variations: null, selectedVariation: null })
      try {
        const res = await api.variations(prompt, code, provider, model || undefined, autoRefine)
        if (res.action === 'clarify') {
          set({ pending: { originalPrompt: prompt, questions: res.questions } })
          return
        }
        if (res.action === 'invalid') {
          set({ invalidNotice: { originalPrompt: prompt, reason: res.reason ?? 'Inconsistent request.' } })
          return
        }
        set({
          variations: { candidates: res.candidates, originalPrompt: prompt, refined: res.refined_prompt },
        })
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
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
          set({ code: step.code, stlBase64: step.stl_base64, geometryInfo: step.geometry_info })
        }
      } catch (e) {
        set({ error: String(e) })
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
          set({ stlBase64: step.stl_base64, geometryInfo: step.geometry_info, code: step.code, error: null })
        } else {
          set({ error: step.error })
        }
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },

    async revert(stepId) {
      set({ busy: true, error: null })
      try {
        applySession(await api.revert(stepId))
      } catch (e) {
        set({ error: String(e) })
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
        set({ error: String(e) })
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
        set({ error: `Could not load project: ${e}` })
      } finally {
        set({ busy: false })
      }
    },
  }
})
