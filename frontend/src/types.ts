export type SpecificationStatus = 'confirmed' | 'needs_input' | 'assumed' | 'conflicted'

export interface Dimension {
  id: string
  label: string
  value: number | string | null
  unit: string
  confidence: number
  status: SpecificationStatus
  critical: boolean
  min?: number | null
  max?: number | null
  alternatives: Array<number | string>
  evidence: string[]
}

export interface Feature {
  id: string
  label: string
  type: string
  status: SpecificationStatus | 'unsupported'
  confidence: number
  evidence: string[]
}

export interface Question {
  id: string
  field_id: string
  prompt: string
  alternatives: Array<number | string>
  required: boolean
}

export interface Annotation {
  id: string
  field_id: string
  field_ids: string[]
  x: number
  y: number
  label: string
}

export interface DraftSpecification {
  id: string
  title: string
  units: string
  dimensions: Dimension[]
  features: Feature[]
  assumptions: Array<{ id: string; rationale: string; status: SpecificationStatus }>
  questions: Question[]
  annotations: Annotation[]
  free_text: string
  [key: string]: unknown
}

export interface Project {
  id: string
  title: string
  generation?: {
    render_artifacts?: Record<string, { image_data: string }>
    error?: { stage?: string; message?: string }
  }
  [key: string]: unknown
}

export interface ApiError {
  message: string
  fieldIds: string[]
  stage?: string
  requestId?: string
}
