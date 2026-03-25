/* ------------------------------------------------------------------ */
/*  TypeScript types mirroring Pydantic models from models.py          */
/* ------------------------------------------------------------------ */

// -- Analysis domain ------------------------------------------------

export interface JiraMatch {
  key: string
  summary: string
  status: string
  priority: string
  url: string
  score: number
}

export interface ProductBugReport {
  title: string
  severity: string
  component: string
  description: string
  evidence: string
  jira_search_keywords: string[]
  jira_matches: JiraMatch[]
}

export interface CodeFix {
  file: string
  line: string
  change: string
}

export interface AnalysisDetail {
  classification: string
  affected_tests: string[]
  details: string
  artifacts_evidence: string
  code_fix?: CodeFix
  product_bug_report?: ProductBugReport
}

export interface FailureAnalysis {
  test_name: string
  error: string
  analysis: AnalysisDetail
  error_signature: string
}

export interface ChildJobAnalysis {
  job_name: string
  build_number: number
  jenkins_url: string | null
  summary: string | null
  failures: FailureAnalysis[]
  failed_children: ChildJobAnalysis[]
  note: string | null
}

export interface AnalysisResult {
  job_id: string
  job_name: string
  build_number: number
  jenkins_url: string | null
  status: 'pending' | 'running' | 'completed' | 'failed'
  summary: string
  ai_provider: string
  ai_model: string
  failures: FailureAnalysis[]
  child_job_analyses: ChildJobAnalysis[]
  error?: string
}

// -- Dashboard ------------------------------------------------------

export interface DashboardJob {
  job_id: string
  jenkins_url: string | null
  status: string
  created_at: string
  completed_at?: string | null
  analysis_started_at?: string | null
  reviewed_count: number
  comment_count: number
  job_name?: string
  build_number?: number
  failure_count?: number
  child_job_count?: number
  error?: string
}

// -- Comments & Reviews ---------------------------------------------

export interface Comment {
  id: number
  job_id: string
  test_name: string
  child_job_name: string
  child_build_number: number
  comment: string
  username: string
  created_at: string
}

export interface ReviewState {
  reviewed: boolean
  username: string
  updated_at: string
}

export interface CommentsAndReviews {
  comments: Comment[]
  reviews: Record<string, ReviewState>
}

// -- Bug creation ---------------------------------------------------

export interface SimilarIssue {
  number: number | null
  key: string
  title: string
  url: string
  status: string
}

export interface PreviewIssueResponse {
  title: string
  body: string
  similar_issues: SimilarIssue[]
}

export interface CreateIssueResponse {
  url: string
  key: string
  number: number
  title: string
  comment_id: number
}

// -- History --------------------------------------------------------

export interface FailureHistoryEntry {
  id: number
  job_id: string
  job_name: string
  build_number: number
  test_name: string
  error_message: string
  error_signature: string
  classification: string
  child_job_name: string
  child_build_number: number
  analyzed_at: string
}

export interface TestHistory {
  total_runs: number
  failures: number
  passes: number | null
  failure_rate: number | null
  first_seen: string
  last_seen: string
  last_classification: string
  classifications: Record<string, number>
  recent_runs: Array<{
    job_id: string
    job_name: string
    build_number: number
    classification: string
    analyzed_at: string
    child_job_name: string
    child_build_number: number
    error_message: string
  }>
  comments: Array<{ comment: string; username: string; created_at: string }>
  consecutive_failures: number
  note?: string
}

// -- Grouping -------------------------------------------------------

export interface GroupedFailure {
  signature: string
  tests: FailureAnalysis[]
  count: number
  id: string
}

// -- AI Config ------------------------------------------------------

export interface AiConfig {
  ai_provider: string
  ai_model: string
}

// -- Result wrapper (from GET /results/{jobId}) ---------------------

export interface ResultResponse {
  job_id: string
  status: string
  result: AnalysisResult | null
  created_at: string
  completed_at?: string
  analysis_started_at?: string | null
}

// -- Comment enrichment ---------------------------------------------

export interface CommentEnrichment {
  type: 'github_pr' | 'github_issue' | 'jira'
  key: string
  status: string
}
