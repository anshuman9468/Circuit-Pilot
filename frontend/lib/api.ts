export type ProjectSummary = {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
};

export type Project = ProjectSummary & { requirements: string };

export type ProjectFile = {
  id: string;
  path: string;
  size_bytes: number;
  created_at: string;
  updated_at: string;
};

export type FileDocument = ProjectFile & { content: string };

export type ActivityEvent = {
  id: string;
  event_type: string;
  summary: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type Results = {
  available: boolean;
  message: string;
  runs: ExecutionRun[];
};

export type ToolchainStatus = {
  runtime_requested: string;
  runtime: string | null;
  runtime_available: boolean;
  runtime_version: string | null;
  image: string;
  image_available: boolean;
  tool_versions: Record<string, string>;
  checked_at: string;
  message: string;
  setup_instructions: string[];
};

export type ExecutionManifest = {
  project_id: string;
  rtl_paths: string[];
  testbench_paths: string[];
  include_dirs: string[];
  top_candidates: string[];
  testbench_top_candidates: string[];
};

export type Diagnostic = {
  severity: "info" | "warning" | "error" | "unknown";
  message: string;
  raw: string | null;
  file: string | null;
  line: number | null;
  column: number | null;
};

export type TestbenchReport = {
  status: "passed" | "failed" | "unknown";
  checks_total: number;
  checks_passed: number;
  checks_failed: number;
  check_ids: string[];
  completion_marker: string | null;
  limitation: string | null;
};

export type ExecutionStage = {
  stage: string;
  status: string;
  tool: string;
  tool_version: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  exit_code: number | null;
  termination_reason: string | null;
  diagnostics: Diagnostic[];
  testbench_report: TestbenchReport | null;
  metrics: Record<string, number | string>;
  artifact_ids: string[];
  log_artifact_id: string | null;
  log_truncated: boolean;
};

export type ExecutionEvent = {
  id: string;
  stage_name: string | null;
  event_type: string;
  summary: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type ExecutionArtifact = {
  id: string;
  kind: string;
  name: string | null;
  size_bytes: number | null;
  sha256: string | null;
  content_type: string | null;
  stage: string | null;
  created_at: string;
};

export type ExecutionRun = {
  id: string;
  project_id: string;
  status: string;
  stage: string;
  snapshot_id: string | null;
  specification_revision: number | null;
  generation_proposal_id: string | null;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  stages: ExecutionStage[];
  events: ExecutionEvent[];
  artifacts: ExecutionArtifact[];
};

export type ModelConfig = {
  configured: boolean;
  status: "not_configured" | "configured_not_tested" | "connected" | "connection_failed" | string;
  model: string;
  base_url: string;
  message: string;
  last_error: string | null;
  provider: string;
  fallback_configured: boolean;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  run_id: string | null;
};

export type Usage = {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
};

export type RunEvent = {
  id: string;
  event_type: string;
  summary: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type AgentRun = {
  id: string;
  project_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted" | "incomplete" | string;
  model: string;
  rounds: number;
  tool_calls: number;
  usage: Usage;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  events: RunEvent[];
};

export type Conversation = {
  conversation_id: string;
  messages: ConversationMessage[];
  latest_run: AgentRun | null;
};

export type ConnectionTest = {
  status: string;
  model: string;
  message: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
};

export type SpecificationSource = "user" | "assumption" | "planner";
export type DesignCategory = "combinational" | "sequential" | "mixed" | "unspecified";
export type SpecificationStatus = "draft" | "ready";
export type Width = number | string | null;

export type SpecificationParameter = {
  id: string;
  name: string;
  default_value: number | string | null;
  width: Width;
  constraints: string[];
  notes: string;
  source: SpecificationSource;
};

export type SpecificationPort = {
  id: string;
  name: string;
  direction: "input" | "output" | "inout" | "unspecified";
  width: Width;
  signedness: "signed" | "unsigned" | "unspecified";
  purpose: string;
  source: SpecificationSource;
};

export type ClockDomain = {
  id: string;
  name: string;
  signal: string;
  frequency_hz: number | null;
  edge: "rising" | "falling" | "either" | "unspecified";
  source: SpecificationSource;
};

export type ResetSignal = {
  id: string;
  name: string;
  signal: string;
  polarity: "active_high" | "active_low" | "unspecified";
  behavior: "synchronous" | "asynchronous" | "unspecified";
  associated_clock_domain: string | null;
  intended_reset_state: number | null;
  source: SpecificationSource;
};

export type FunctionalRequirement = {
  id: string;
  title: string;
  statement: string;
  source: SpecificationSource;
};

export type TimingExpectations = {
  latency_cycles: number | null;
  max_latency_cycles: number | null;
  throughput: string;
  notes: string;
  source: SpecificationSource;
};

export type BoundaryBehavior = {
  id: string;
  condition: string;
  behavior: string;
  source: SpecificationSource;
};

export type Assumption = {
  id: string;
  text: string;
  accepted: boolean;
  source: SpecificationSource;
};

export type OpenQuestion = {
  id: string;
  question: string;
  related_field: string;
  severity: "blocking" | "advisory";
  status: "open" | "resolved";
  answer: string;
  source: SpecificationSource;
};

export type ModuleConnection = {
  id: string;
  from_module: string;
  from_port: string;
  to_module: string;
  to_port: string;
  notes: string;
  source: SpecificationSource;
};

export type ModulePlan = {
  id: string;
  name: string;
  responsibility: string;
  input_ports: string[];
  output_ports: string[];
  requirement_ids: string[];
  internal_state: string;
  dependencies: string[];
  connections: ModuleConnection[];
  design_notes: string;
  is_top_level: boolean;
  source: SpecificationSource;
};

export type VerificationObjective = {
  id: string;
  title: string;
  requirement_ids: string[];
  preconditions: string;
  stimulus: string;
  expected_behavior: string;
  check_type: "directed" | "boundary" | "reset" | "sequence" | "invariant" | "unspecified";
  notes: string;
  source: SpecificationSource;
};

export type CircuitSpecification = {
  specification_id: string;
  project_id: string;
  schema_version: string;
  revision: number;
  status: SpecificationStatus;
  created_at: string | null;
  updated_at: string | null;
  original_request: string;
  circuit_name: string;
  purpose: string;
  design_category: DesignCategory;
  parameters: SpecificationParameter[];
  ports: SpecificationPort[];
  clock_domains: ClockDomain[];
  reset_intent: "required" | "not_intended" | "unspecified";
  reset_signals: ResetSignal[];
  functional_requirements: FunctionalRequirement[];
  timing: TimingExpectations | null;
  boundary_behaviors: BoundaryBehavior[];
  assumptions: Assumption[];
  open_questions: OpenQuestion[];
  modules: ModulePlan[];
  verification_objectives: VerificationObjective[];
};

export type Clarification = {
  id: string;
  related_field: string;
  field_path: string;
  reason: string;
  severity: "blocking" | "advisory";
  expected_answer_type: "text" | "choice" | "positive_integer_or_parameter" | "boolean" | "object";
  status: "open" | "resolved";
  code: string;
};

export type ValidationReport = {
  valid: boolean;
  blocking_count: number;
  advisory_count: number;
  generated_at: string;
  findings: Clarification[];
  limitation: string;
};

export type SpecificationResponse = {
  specification: CircuitSpecification;
  validation: ValidationReport;
  status: SpecificationStatus;
  current_revision: number;
  ready_revision: number | null;
};

export type SpecificationRevisionSummary = {
  revision: number;
  status: SpecificationStatus;
  schema_version: string;
  created_at: string;
  updated_at: string;
  ready_at: string | null;
};

export type SpecificationRevisionResponse = {
  revision_id: string;
  specification: CircuitSpecification;
  validation: ValidationReport;
  status: SpecificationStatus;
  current_revision: number;
  ready_revision: number | null;
};

export type SpecificationPlannerConfig = {
  enabled: boolean;
  configured: boolean;
  status: "disabled" | "not_configured" | "configured" | string;
  provider: string;
  model: string;
  message: string;
};

export type PlannerProposal = {
  proposed_specification: CircuitSpecification;
  assumptions: Assumption[];
  clarification_questions: OpenQuestion[];
  proposed_modules: ModulePlan[];
  proposed_verification_objectives: VerificationObjective[];
  summary: string;
};

export type SpecificationPlannerEvent = {
  id: string;
  event_type: string;
  summary: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type SpecificationPlannerRun = {
  id: string;
  project_id: string;
  base_revision: number;
  user_instruction: string;
  state: "queued" | "running" | "proposed" | "accepted" | "discarded" | "failed" | "cancelled" | "interrupted" | string;
  model: string;
  rounds: number;
  correction_attempts: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  proposal: PlannerProposal | null;
  validation: ValidationReport | null;
  current_revision: number | null;
  accepted_revision: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  accepted_at: string | null;
  events: SpecificationPlannerEvent[];
};

export type SpecificationPlannerSummary = {
  id: string;
  project_id: string;
  base_revision: number;
  state: string;
  model: string;
  rounds: number;
  proposal_available: boolean;
  created_at: string;
  updated_at: string;
};

export type GenerationConfig = {
  enabled: boolean;
  configured: boolean;
  status: "disabled" | "not_configured" | "configured" | string;
  model: string;
  message: string;
};

export type GenerationEvent = {
  id: string;
  event_type: string;
  summary: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type GenerationClarification = {
  id: string;
  related_field: string;
  reason: string;
  severity: "blocking" | "advisory";
  expected_answer_type: "text" | "choice" | "object";
};

export type GenerationJob = {
  id: string;
  project_id: string;
  specification_revision: number;
  state: "queued" | "running" | "awaiting_clarification" | "proposed" | "applied" | "discarded" | "failed" | "cancelled" | "interrupted" | string;
  model: string;
  rounds: number;
  correction_attempts: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  clarification: GenerationClarification[];
  proposal_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  events: GenerationEvent[];
};

export type ProposedFile = {
  path: string;
  role: "rtl" | "testbench" | "documentation" | string;
  language: string;
  content: string;
};

export type GenerationResult = {
  top_module: string;
  testbench_top: string;
  files: ProposedFile[];
  source_manifest: { path: string; role: "rtl" | "testbench"; order: number }[];
  requirement_module_mapping: Record<string, string[]>;
  verification_test_mapping: Record<string, string[]>;
  unmapped_requirements: string[];
  assumptions: string[];
  unresolved_questions: string[];
  known_limitations: string[];
  implementation_summary: string;
};

export type GenerationFileDiff = {
  path: string;
  role: string;
  change_type: "added" | "modified" | "unchanged" | string;
  existing_content: string | null;
  proposed_content: string;
};

export type GenerationProposal = {
  proposal_id: string;
  generation_id: string;
  project_id: string;
  status: string;
  specification_revision: number;
  baseline_revision: string;
  result: GenerationResult;
  files: GenerationFileDiff[];
  assumptions: string[];
  unresolved_questions: string[];
  known_limitations: string[];
  backup_count: number;
  created_at: string;
  updated_at: string;
};

const configuredBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const API_BASE = configuredBase.endsWith("/") ? configuredBase.slice(0, -1) : configuredBase;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch {
    throw new Error("The backend is not reachable. Start the FastAPI service and try again.");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const baseMessage = body?.error?.message || "The request could not be completed.";
    const details = body?.error?.details;
    const detailMessage = Array.isArray(details)
      ? details.map((item) => `${Array.isArray(item?.location) ? item.location.join(".") : ""}: ${item?.message || "Invalid value"}`).join("; ")
      : "";
    const message = detailMessage ? `${baseMessage} ${detailMessage}` : baseMessage;
    throw new Error(message);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; service: string }>("/health"),
  listProjects: () => request<ProjectSummary[]>("/projects"),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (payload: { name: string; description: string; requirements: string }) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(payload) }),
  updateProject: (id: string, payload: { name?: string; description?: string }) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),
  updateRequirements: (id: string, content: string) =>
    request<{ project_id: string; content: string; updated_at: string }>(`/projects/${id}/requirements`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  listFiles: (id: string) => request<ProjectFile[]>(`/projects/${id}/files`),
  getFile: (projectId: string, fileId: string) => request<FileDocument>(`/projects/${projectId}/files/${fileId}`),
  createFile: (projectId: string, path: string, content: string) =>
    request<FileDocument>(`/projects/${projectId}/files`, {
      method: "POST",
      body: JSON.stringify({ path, content }),
    }),
  updateFile: (projectId: string, fileId: string, payload: { content?: string; path?: string }) =>
    request<FileDocument>(`/projects/${projectId}/files/${fileId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteFile: (projectId: string, fileId: string) =>
    request<void>(`/projects/${projectId}/files/${fileId}`, { method: "DELETE" }),
  activity: (id: string) => request<ActivityEvent[]>(`/projects/${id}/activity`),
  results: (id: string) => request<Results>(`/projects/${id}/results`),
  toolchainStatus: () => request<ToolchainStatus>("/toolchain/status"),
  executionManifest: (id: string) => request<ExecutionManifest>(`/projects/${id}/execution/manifest`),
  listExecutionRuns: (id: string) => request<ExecutionRun[]>(`/projects/${id}/execution/runs`),
  startExecution: (id: string, payload: {
    stage: "lint" | "simulation" | "synthesis" | "checks";
    specification_revision?: number | null;
    generation_proposal_id?: string | null;
    rtl_paths: string[];
    testbench_paths?: string[];
    include_dirs?: string[];
    top_module: string;
    testbench_top?: string | null;
    parameters?: Record<string, string | number>;
  }) => request<ExecutionRun>(`/projects/${id}/execution/runs`, { method: "POST", body: JSON.stringify(payload) }),
  getExecutionRun: (projectId: string, runId: string) => request<ExecutionRun>(`/projects/${projectId}/execution/runs/${runId}`),
  cancelExecution: (projectId: string, runId: string) => request<ExecutionRun>(`/projects/${projectId}/execution/runs/${runId}/cancel`, { method: "POST" }),
  executionArtifactUrl: (projectId: string, runId: string, artifactId: string) => `${API_BASE}/projects/${projectId}/execution/runs/${runId}/artifacts/${artifactId}`,
  modelConfig: () => request<ModelConfig>("/config/model"),
  testModelConnection: () => request<ConnectionTest>("/config/model/test", { method: "POST" }),
  getConversation: (id: string) => request<Conversation>(`/projects/${id}/conversation`),
  sendMessage: (id: string, content: string) =>
    request<AgentRun>(`/projects/${id}/conversation/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  getRun: (projectId: string, runId: string) => request<AgentRun>(`/projects/${projectId}/runs/${runId}`),
  cancelRun: (projectId: string, runId: string) =>
    request<AgentRun>(`/projects/${projectId}/runs/${runId}/cancel`, { method: "POST" }),
  retryRun: (projectId: string, runId: string) =>
    request<AgentRun>(`/projects/${projectId}/runs/${runId}/retry`, { method: "POST" }),
  getSpecification: (id: string) => request<SpecificationResponse>(`/projects/${id}/specification`),
  saveSpecification: (id: string, expectedRevision: number, specification: CircuitSpecification) =>
    request<SpecificationResponse>(`/projects/${id}/specification`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: expectedRevision, specification }),
    }),
  validateSpecification: (id: string, specification: CircuitSpecification) =>
    request<SpecificationResponse>(`/projects/${id}/specification/validate`, {
      method: "POST",
      body: JSON.stringify({ specification }),
    }),
  listSpecificationRevisions: (id: string) =>
    request<SpecificationRevisionSummary[]>(`/projects/${id}/specification/revisions`),
  getSpecificationRevision: (id: string, revision: number) =>
    request<SpecificationRevisionResponse>(`/projects/${id}/specification/revisions/${revision}`),
  answerSpecificationClarification: (id: string, clarificationId: string, expectedRevision: number, answer: unknown) =>
    request<SpecificationResponse>(`/projects/${id}/specification/clarifications/${encodeURIComponent(clarificationId)}`, {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, answer }),
    }),
  markSpecificationReady: (id: string, expectedRevision: number) =>
    request<SpecificationResponse>(`/projects/${id}/specification/ready`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  exportSpecification: (id: string) =>
    request<{ specification: CircuitSpecification; validation: ValidationReport; exported_at: string }>(`/projects/${id}/specification/export`),
  specificationPlannerConfig: () => request<SpecificationPlannerConfig>("/config/specification-planner"),
  startSpecificationPlanner: (projectId: string, expectedRevision: number, instruction = "") =>
    request<SpecificationPlannerRun>(`/projects/${projectId}/specification/planner`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision, instruction }),
    }),
  listSpecificationPlannerRuns: (projectId: string) =>
    request<SpecificationPlannerSummary[]>(`/projects/${projectId}/specification/planner`),
  getSpecificationPlannerRun: (projectId: string, plannerId: string) =>
    request<SpecificationPlannerRun>(`/projects/${projectId}/specification/planner/${plannerId}`),
  cancelSpecificationPlannerRun: (projectId: string, plannerId: string) =>
    request<SpecificationPlannerRun>(`/projects/${projectId}/specification/planner/${plannerId}/cancel`, { method: "POST" }),
  acceptSpecificationPlannerRun: (projectId: string, plannerId: string, expectedRevision: number) =>
    request<SpecificationPlannerRun>(`/projects/${projectId}/specification/planner/${plannerId}/accept`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  discardSpecificationPlannerRun: (projectId: string, plannerId: string) =>
    request<SpecificationPlannerRun>(`/projects/${projectId}/specification/planner/${plannerId}/discard`, { method: "POST" }),
  generationConfig: () => request<GenerationConfig>("/config/generation"),
  startGeneration: (projectId: string, specificationRevision: number, settings?: { include_existing_context: boolean; context_file_ids: string[] }) =>
    request<GenerationJob>(`/projects/${projectId}/generations`, {
      method: "POST",
      body: JSON.stringify({ specification_revision: specificationRevision, settings: settings || { include_existing_context: false, context_file_ids: [] } }),
    }),
  listGenerations: (projectId: string) => request<GenerationJob[]>(`/projects/${projectId}/generations`),
  getGeneration: (projectId: string, generationId: string) => request<GenerationJob>(`/projects/${projectId}/generations/${generationId}`),
  cancelGeneration: (projectId: string, generationId: string) =>
    request<GenerationJob>(`/projects/${projectId}/generations/${generationId}/cancel`, { method: "POST" }),
  listGenerationProposals: (projectId: string) => request<GenerationProposal[]>(`/projects/${projectId}/generation-proposals`),
  getGenerationProposal: (projectId: string, proposalId: string) => request<GenerationProposal>(`/projects/${projectId}/generation-proposals/${proposalId}`),
  acceptGenerationProposal: (projectId: string, proposalId: string) =>
    request<GenerationProposal>(`/projects/${projectId}/generation-proposals/${proposalId}/accept`, { method: "POST" }),
  discardGenerationProposal: (projectId: string, proposalId: string) =>
    request<GenerationProposal>(`/projects/${projectId}/generation-proposals/${proposalId}/discard`, { method: "POST" }),
};
