import { create } from 'zustand'
import type { ApiError, DraftSpecification, LintResult, Project, ReviewPlanItem } from './types'

type RequestState = 'idle' | 'analyzing' | 'validating' | 'building' | 'exporting'

interface AppState {
  specification: DraftSpecification | null
  project: Project | null
  sourceUrl: string | null
  draftValues: Record<string, number | string>
  acceptedFeatureIds: string[]
  acceptedAssumptionIds: string[]
  selectedId: string | null
  clarifications: Record<string, string>
  requestState: RequestState
  validationPassed: boolean
  error: ApiError | null
  lint: LintResult
  reviewPlan: ReviewPlanItem[]
  setSpecification: (specification: DraftSpecification) => void
  setSourceUrl: (sourceUrl: string | null) => void
  setDraftValue: (id: string, value: number | string) => void
  toggleFeature: (id: string) => void
  toggleAssumption: (id: string) => void
  setSelectedId: (id: string | null) => void
  setClarification: (questionId: string, text: string) => void
  setRequestState: (requestState: RequestState) => void
  setError: (error: ApiError | null) => void
  setValidationPassed: (validationPassed: boolean) => void
  setProject: (project: Project | null) => void
  setReviewData: (lint?: LintResult, reviewPlan?: ReviewPlanItem[]) => void
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
  clarifications: {},
  requestState: 'idle',
  validationPassed: false,
  error: null,
  lint: { issues: [], unevaluated_feature_ids: [] },
  reviewPlan: [],
  setSpecification: (specification) => set({ specification, project: null, draftValues: {}, acceptedFeatureIds: [], acceptedAssumptionIds: [], validationPassed: false, error: null }),
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
  setClarification: (questionId, text) => set((state) => ({ clarifications: { ...state.clarifications, [questionId]: text }, project: null, validationPassed: false })),
  setRequestState: (requestState) => set({ requestState }),
  setError: (error) => set({ error }),
  setValidationPassed: (validationPassed) => set({ validationPassed }),
  setProject: (project) => set({ project }),
  setReviewData: (lint = { issues: [], unevaluated_feature_ids: [] }, reviewPlan = []) => set({ lint, reviewPlan }),
  reset: () => set({ specification: null, project: null, sourceUrl: null, draftValues: {}, acceptedFeatureIds: [], acceptedAssumptionIds: [], selectedId: null, clarifications: {}, requestState: 'idle', validationPassed: false, error: null, lint: { issues: [], unevaluated_feature_ids: [] }, reviewPlan: [] }),
}))
