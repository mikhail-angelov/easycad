import { create } from 'zustand'
import type { ApiError, DraftSpecification, Project } from './types'

type RequestState = 'idle' | 'analyzing' | 'validating' | 'building' | 'exporting'

interface AppState {
  specification: DraftSpecification | null
  project: Project | null
  sourceUrl: string | null
  draftValues: Record<string, number | string>
  acceptedFeatureIds: string[]
  acceptedAssumptionIds: string[]
  selectedId: string | null
  freeText: string
  clarificationQuestionId: string | null
  requestState: RequestState
  validationPassed: boolean
  error: ApiError | null
  setSpecification: (specification: DraftSpecification) => void
  setSourceUrl: (sourceUrl: string | null) => void
  setDraftValue: (id: string, value: number | string) => void
  toggleFeature: (id: string) => void
  toggleAssumption: (id: string) => void
  setSelectedId: (id: string | null) => void
  setFreeText: (freeText: string) => void
  setClarificationQuestionId: (questionId: string | null) => void
  setRequestState: (requestState: RequestState) => void
  setError: (error: ApiError | null) => void
  setValidationPassed: (validationPassed: boolean) => void
  setProject: (project: Project | null) => void
  reset: () => void
}

export const useAppStore = create<AppState>((set) => ({
  specification: null,
  project: null,
  sourceUrl: null,
  draftValues: {},
  acceptedFeatureIds: [],
  acceptedAssumptionIds: [],
  selectedId: null,
  freeText: '',
  clarificationQuestionId: null,
  requestState: 'idle',
  validationPassed: false,
  error: null,
  setSpecification: (specification) => set({ specification, project: null, draftValues: {}, acceptedFeatureIds: [], acceptedAssumptionIds: [], freeText: '', clarificationQuestionId: null, validationPassed: false, error: null }),
  setSourceUrl: (sourceUrl) => set({ sourceUrl }),
  setDraftValue: (id, value) => set((state) => ({ draftValues: { ...state.draftValues, [id]: value }, project: null, validationPassed: false })),
  toggleFeature: (id) => set((state) => ({
    acceptedFeatureIds: state.acceptedFeatureIds.includes(id)
      ? state.acceptedFeatureIds.filter((value) => value !== id)
      : [...state.acceptedFeatureIds, id],
    project: null,
    validationPassed: false,
  })),
  toggleAssumption: (id) => set((state) => ({
    acceptedAssumptionIds: state.acceptedAssumptionIds.includes(id)
      ? state.acceptedAssumptionIds.filter((value) => value !== id)
      : [...state.acceptedAssumptionIds, id],
    project: null,
    validationPassed: false,
  })),
  setSelectedId: (selectedId) => set({ selectedId }),
  setFreeText: (freeText) => set({ freeText, project: null, validationPassed: false }),
  setClarificationQuestionId: (clarificationQuestionId) => set({ clarificationQuestionId }),
  setRequestState: (requestState) => set({ requestState }),
  setError: (error) => set({ error }),
  setValidationPassed: (validationPassed) => set({ validationPassed }),
  setProject: (project) => set({ project }),
  reset: () => set({ specification: null, project: null, sourceUrl: null, draftValues: {}, acceptedFeatureIds: [], acceptedAssumptionIds: [], selectedId: null, freeText: '', clarificationQuestionId: null, requestState: 'idle', validationPassed: false, error: null }),
}))
