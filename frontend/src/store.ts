import { create } from 'zustand'
import { api, type SessionPayload, type Step } from './api'

export interface ChatEntry {
  id: number
  prompt: string
  refined: string | null
  ok: boolean
  error: string | null
}

interface State {
  steps: Step[]
  currentId: number | null
  code: string // editor contents
  stlBase64: string | null // model currently shown in the viewer
  geometryInfo: string | null
  providers: Record<string, string>
  provider: string
  chatLog: ChatEntry[]
  busy: boolean
  error: string | null

  init: () => Promise<void>
  setCode: (code: string) => void
  setProvider: (provider: string) => void
  sendChat: (prompt: string) => Promise<void>
  runManual: () => Promise<void>
  revert: (stepId: number) => Promise<void>
  reset: () => Promise<void>
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

  return {
    steps: [],
    currentId: null,
    code: '',
    stlBase64: null,
    geometryInfo: null,
    providers: {},
    provider: 'deepseek',
    chatLog: [],
    busy: false,
    error: null,

    async init() {
      set({ busy: true, error: null })
      try {
        const session = await api.session()
        applySession(session)
        set({ provider: session.default_provider })
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },

    setCode: (code) => set({ code }),
    setProvider: (provider) => set({ provider }),

    async sendChat(prompt) {
      const { code, provider } = get()
      set({ busy: true, error: null })
      try {
        const { step, session } = await api.chat(prompt, code, provider)
        set({
          steps: session.steps,
          currentId: session.current_id,
          chatLog: [
            ...get().chatLog,
            { id: step.id, prompt, refined: step.refined_prompt, ok: step.success, error: step.error },
          ],
        })
        if (step.success) {
          // Advance editor + viewer to the new model.
          set({ code: step.code, stlBase64: step.stl_base64, geometryInfo: step.geometry_info, error: null })
        } else {
          // Keep the previous model in the viewer; show the failed code so the
          // user can fix it, and surface the error.
          set({ code: step.code, error: step.error })
        }
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
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
        set({ chatLog: [] })
      } catch (e) {
        set({ error: String(e) })
      } finally {
        set({ busy: false })
      }
    },
  }
})
