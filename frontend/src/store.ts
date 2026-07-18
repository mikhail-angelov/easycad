import { create } from 'zustand'
import type { ApiError, DraftSpecification, ModelResponse, Project } from './types'

type RequestState = 'idle' | 'working'

interface AppState {
  specification: DraftSpecification | null
  model: Project | null
  modelStl: string | null
  description: string
  requestState: RequestState
  error: ApiError | null
  setModelResponse: (response: ModelResponse) => void
  setRequestState: (state: RequestState) => void
  setError: (error: ApiError | null) => void
  reset: () => void
}

export const useAppStore = create<AppState>((set) => ({
  specification: null,
  model: null,
  modelStl: null,
  description: '',
  requestState: 'idle',
  error: null,
  setModelResponse: (response) => set({
    specification: response.specification, model: response.model, modelStl: response.model_stl,
    description: response.description, error: null,
  }),
  setRequestState: (requestState) => set({ requestState }),
  setError: (error) => set({ error }),
  reset: () => set({ specification: null, model: null, modelStl: null, description: '', requestState: 'idle', error: null }),
}))
