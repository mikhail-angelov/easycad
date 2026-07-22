import { create } from 'zustand'
import { nextAlias } from './alias'
import type { ApiError, DraftSpecification, FeatureRosterEntry, ModelResponse, Project } from './types'

type RequestState = 'idle' | 'working'

export interface AppState {
  specification: DraftSpecification | null
  model: Project | null
  modelStl: string | null
  description: string
  features: FeatureRosterEntry[]
  featureAliases: Record<string, string>
  selectedId: string | null
  requestState: RequestState
  error: ApiError | null
  setModelResponse: (response: ModelResponse) => void
  setSelectedId: (id: string | null) => void
  setRequestState: (state: RequestState) => void
  setError: (error: ApiError | null) => void
  reset: () => void
}

export const useAppStore = create<AppState>((set) => ({
  specification: null,
  model: null,
  modelStl: null,
  description: '',
  features: [],
  featureAliases: {},
  selectedId: null,
  requestState: 'idle',
  error: null,
  setModelResponse: (response) => set((state) => {
    const featureAliases = { ...state.featureAliases }
    for (const feature of response.features) {
      if (!(feature.id in featureAliases)) featureAliases[feature.id] = nextAlias(Object.keys(featureAliases).length)
    }
    return {
      specification: response.specification, model: response.model, modelStl: response.model_stl,
      description: response.description, features: response.features, featureAliases, error: null,
    }
  }),
  setSelectedId: (selectedId) => set({ selectedId }),
  setRequestState: (requestState) => set({ requestState }),
  setError: (error) => set({ error }),
  reset: () => set({
    specification: null, model: null, modelStl: null, description: '', features: [], featureAliases: {},
    selectedId: null, requestState: 'idle', error: null,
  }),
}))
