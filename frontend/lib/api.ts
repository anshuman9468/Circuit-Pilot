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
  runs: unknown[];
};

export type ModelConfig = {
  configured: boolean;
  status: "not_configured" | "configured_not_tested" | "connected" | "connection_failed" | string;
  model: string;
  base_url: string;
  message: string;
  last_error: string | null;
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
    const message = body?.error?.message || "The request could not be completed.";
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
};
