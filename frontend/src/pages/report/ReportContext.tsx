import { createContext, useContext, useReducer, useRef, useCallback, type Dispatch, type ReactNode } from 'react'
import { api } from '@/lib/api'
import type { AnalysisResult, Comment, ReviewState, CommentsAndReviews, AiConfig, CommentEnrichment } from '@/types'

interface ReportState {
  result: AnalysisResult | null
  createdAt: string
  completedAt: string
  analysisStartedAt: string
  comments: Comment[]
  reviews: Record<string, ReviewState>
  enrichments: Record<string, CommentEnrichment[]>
  classifications: Record<string, string>
  githubAvailable: boolean
  jiraAvailable: boolean
  aiConfigs: AiConfig[]
  loading: boolean
  error: string
}

type ReportAction =
  | { type: 'SET_RESULT'; payload: { result: AnalysisResult; createdAt: string; completedAt: string; analysisStartedAt: string } }
  | { type: 'SET_COMMENTS_AND_REVIEWS'; payload: CommentsAndReviews }
  | { type: 'ADD_COMMENT'; payload: Comment }
  | { type: 'REMOVE_COMMENT'; payload: number }
  | { type: 'SET_REVIEW'; payload: { key: string; state: ReviewState } }
  | { type: 'SET_GITHUB_AVAILABLE'; payload: boolean }
  | { type: 'SET_JIRA_AVAILABLE'; payload: boolean }
  | { type: 'SET_AI_CONFIGS'; payload: AiConfig[] }
  | { type: 'SET_ENRICHMENTS'; payload: Record<string, CommentEnrichment[]> }
  | { type: 'SET_CLASSIFICATIONS'; payload: Record<string, string> }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string }
  | {
      type: 'OVERRIDE_CLASSIFICATION'
      payload: {
        testName: string
        classification: string
        childJobName?: string
        childBuildNumber?: number
      }
    }

const initialState: ReportState = {
  result: null,
  createdAt: '',
  completedAt: '',
  analysisStartedAt: '',
  comments: [],
  reviews: {},
  enrichments: {},
  classifications: {},
  githubAvailable: false,
  jiraAvailable: false,
  aiConfigs: [],
  loading: true,
  error: '',
}

function reportReducer(state: ReportState, action: ReportAction): ReportState {
  switch (action.type) {
    case 'SET_RESULT':
      return { ...state, result: action.payload.result, createdAt: action.payload.createdAt, completedAt: action.payload.completedAt, analysisStartedAt: action.payload.analysisStartedAt, loading: false }
    case 'SET_COMMENTS_AND_REVIEWS':
      return { ...state, comments: action.payload.comments, reviews: action.payload.reviews }
    case 'ADD_COMMENT':
      return { ...state, comments: [...state.comments, action.payload] }
    case 'REMOVE_COMMENT':
      return { ...state, comments: state.comments.filter((c) => c.id !== action.payload) }
    case 'SET_REVIEW':
      return { ...state, reviews: { ...state.reviews, [action.payload.key]: action.payload.state } }
    case 'SET_GITHUB_AVAILABLE':
      return { ...state, githubAvailable: action.payload }
    case 'SET_JIRA_AVAILABLE':
      return { ...state, jiraAvailable: action.payload }
    case 'SET_AI_CONFIGS':
      return { ...state, aiConfigs: action.payload }
    case 'SET_ENRICHMENTS':
      return { ...state, enrichments: action.payload }
    case 'SET_CLASSIFICATIONS':
      return { ...state, classifications: action.payload }
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    case 'SET_ERROR':
      return { ...state, error: action.payload, loading: false }
    case 'OVERRIDE_CLASSIFICATION': {
      if (!state.result) return state
      const { testName, classification, childJobName, childBuildNumber } = action.payload
      const patchFailures = (fs: typeof state.result.failures) =>
        (fs ?? []).map((f) =>
          f.test_name === testName ? { ...f, analysis: { ...f.analysis, classification } } : f,
        )
      const patchChildren = (
        cs: typeof state.result.child_job_analyses,
      ): typeof state.result.child_job_analyses =>
        (cs ?? []).map((c) =>
          childJobName && c.job_name === childJobName && c.build_number === childBuildNumber
            ? { ...c, failures: patchFailures(c.failures), failed_children: patchChildren(c.failed_children) }
            : { ...c, failed_children: patchChildren(c.failed_children) },
        )
      const key = reviewKey(testName, childJobName, childBuildNumber)
      return {
        ...state,
        result: {
          ...state.result,
          failures: childJobName ? state.result.failures : patchFailures(state.result.failures),
          child_job_analyses: patchChildren(state.result.child_job_analyses),
        },
        classifications: { ...state.classifications, [key]: classification },
      }
    }
    default:
      return state
  }
}

const StateCtx = createContext<ReportState>(initialState)
const DispatchCtx = createContext<Dispatch<ReportAction>>(() => {})
const RefreshEnrichmentsCtx = createContext<(jobId: string) => void>(() => {})

export function ReportProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reportReducer, initialState)
  const enrichmentSeqRef = useRef(0)

  const refreshEnrichments = useCallback((jobId: string) => {
    const seq = ++enrichmentSeqRef.current
    void api.post<{ enrichments: Record<string, CommentEnrichment[]> }>(`/results/${jobId}/enrich-comments`)
      .then((res) => {
        if (seq === enrichmentSeqRef.current) {
          dispatch({ type: 'SET_ENRICHMENTS', payload: res.enrichments ?? {} })
        }
      })
      .catch(() => {})
  }, [])

  return (
    <StateCtx.Provider value={state}>
      <DispatchCtx.Provider value={dispatch}>
        <RefreshEnrichmentsCtx.Provider value={refreshEnrichments}>{children}</RefreshEnrichmentsCtx.Provider>
      </DispatchCtx.Provider>
    </StateCtx.Provider>
  )
}

export const useReportState = () => useContext(StateCtx)
export const useReportDispatch = () => useContext(DispatchCtx)
export const useRefreshEnrichments = () => useContext(RefreshEnrichmentsCtx)

/** Build the review lookup key matching the backend format. */
export function reviewKey(testName: string, childJobName?: string, childBuildNumber?: number): string {
  if (childJobName && childBuildNumber != null) return `${childJobName}#${childBuildNumber}::${testName}`
  return testName
}
