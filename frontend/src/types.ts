export interface DraftSpecification {
  id: string
  title: string
  units: string
  analysis: { [key: string]: unknown }
  [key: string]: unknown
}

export interface Project { id: string; title: string; [key: string]: unknown }

export interface FeatureRosterEntry {
  id: string
  label: string
  status: 'confirmed' | 'unsupported'
  omission_reason: string | null
  extent: { minimum: [number, number, number]; maximum: [number, number, number] } | null
}

export interface ModelResponse {
  description: string
  specification: DraftSpecification
  model: Project
  model_stl: string
  features: FeatureRosterEntry[]
}

export interface ApiError {
  message: string
  stage?: string
}
