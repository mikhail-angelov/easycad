export interface DraftSpecification {
  id: string
  title: string
  units: string
  analysis: { [key: string]: unknown }
  [key: string]: unknown
}

export interface Project { id: string; title: string; [key: string]: unknown }

export interface ModelResponse {
  description: string
  specification: DraftSpecification
  model: Project
  model_stl: string
}

export interface ApiError {
  message: string
  stage?: string
}
