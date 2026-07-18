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
  parameters?: Record<string, number | string>
  placement?: { origin?: Array<number | string>; plane?: string }
  source_feature_ids?: string[]
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
  exclusions?: Array<{ feature_id: string; source_feature_ids: string[]; reason: string }>
  free_text: string
  [key: string]: unknown
}

export interface LintIssue {
  rule: string
  issue_id: string
  severity: 'error' | 'warning'
  feature_ids: string[]
  message: string
  suggestion?: { feature_id: string; field_path: string; value: number }
}

export interface LintResult { issues: LintIssue[]; unevaluated_feature_ids: string[] }
export interface ReviewPlanItem { tier: number; item_type: string; item_id: string; reason: string }

export interface Project {
  id: string
  title: string
  generation?: {
    semantic_status?: 'not_run' | 'success' | 'failed' | 'draft_preview'
    render_artifacts?: Record<string, { image_data: string }>
    error?: { stage?: string; message?: string; detail?: { feature_ids?: string[]; mismatches?: string[] } }
  }
  [key: string]: unknown
}

export interface ApiError {
  message: string
  fieldIds: string[]
  stage?: string
  requestId?: string
  hints?: string[]
}
