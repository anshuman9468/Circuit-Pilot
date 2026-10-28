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
};

