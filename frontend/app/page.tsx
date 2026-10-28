"use client";

import {
  AlertCircle,
  Boxes,
  Check,
  ChevronRight,
  CircleHelp,
  Code2,
  FileCode2,
  FilePlus2,
  FolderOpen,
  GitBranch,
  History,
  Info,
  Layers3,
  MoreHorizontal,
  Plus,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { ActivityEvent, api, Project, ProjectFile, ProjectSummary, Results } from "../lib/api";

const CodeEditor = dynamic(() => import("../components/CodeEditor"), {
  ssr: false,
  loading: () => <div className="empty-state"><div className="spinner" aria-label="Loading editor" /></div>,
});

const EXAMPLE_REQUIREMENTS = `Design a synchronous, parameterized digital block.

Describe the clock and reset behavior, the input and output signals, configurable parameters, and any timing or protocol requirements. Include edge cases that should be considered during later verification.`;

type Tab = "files" | "activity" | "results";
type Modal = "project" | "file" | "rename" | null;

function readableDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

function relativeDate(value: string): string {
  const distance = Date.now() - new Date(value).getTime();
  const minutes = Math.max(0, Math.floor(distance / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function eventLabel(event: ActivityEvent): string {
  return event.event_type.replaceAll(".", " / ");
}

export default function HomePage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [projectDraft, setProjectDraft] = useState({ name: "", description: "" });
  const [requirementsDraft, setRequirementsDraft] = useState("");
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [activeFile, setActiveFile] = useState<ProjectFile | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [fileDraft, setFileDraft] = useState("");
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [results, setResults] = useState<Results | null>(null);
  const [tab, setTab] = useState<Tab>("files");
  const [modal, setModal] = useState<Modal>(null);
  const [newProject, setNewProject] = useState({ name: "", description: "", requirements: "" });
  const [newFilePath, setNewFilePath] = useState("");
  const [renamePath, setRenamePath] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingProject, setLoadingProject] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [savingRequirements, setSavingRequirements] = useState(false);
  const [savingFile, setSavingFile] = useState(false);
  const [savingDetails, setSavingDetails] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  const requirementsDirty = project ? requirementsDraft !== project.requirements : false;
  const detailsDirty = project ? projectDraft.name !== project.name || projectDraft.description !== project.description : false;
  const fileDirty = activeFile ? fileDraft !== fileContent : false;
  const hasUnsaved = requirementsDirty || detailsDirty || fileDirty;

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsaved) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasUnsaved]);

  async function bootstrap() {
    setLoading(true);
    try {
      const loadedProjects = await api.listProjects();
      setProjects(loadedProjects);
      const stored = window.localStorage.getItem("circuitpilot.activeProject");
      const nextId = stored && loadedProjects.some((item) => item.id === stored) ? stored : loadedProjects[0]?.id;
      if (nextId) await loadProject(nextId);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  async function loadProject(projectId: string) {
    setLoadingProject(true);
    setError(null);
    try {
      const [loadedProject, loadedFiles, loadedEvents, loadedResults] = await Promise.all([
        api.getProject(projectId),
        api.listFiles(projectId),
        api.activity(projectId),
        api.results(projectId),
      ]);
      setActiveProjectId(projectId);
      window.localStorage.setItem("circuitpilot.activeProject", projectId);
      setProject(loadedProject);
      setProjectDraft({ name: loadedProject.name, description: loadedProject.description });
      setRequirementsDraft(loadedProject.requirements);
      setFiles(loadedFiles);
      setEvents(loadedEvents);
      setResults(loadedResults);
      setTab("files");
      const firstFile = loadedFiles[0];
      if (firstFile) {
        const document = await api.getFile(projectId, firstFile.id);
        setActiveFile(firstFile);
        setFileContent(document.content);
        setFileDraft(document.content);
      } else {
        setActiveFile(null);
        setFileContent("");
        setFileDraft("");
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingProject(false);
    }
  }

  function errorMessage(caught: unknown): string {
    return caught instanceof Error ? caught.message : "Something went wrong. Please try again.";
  }

  function confirmDiscard(): boolean {
    if (!hasUnsaved) return true;
    return window.confirm("You have unsaved changes. Leave this project and discard them?");
  }

  async function navigateProject(projectId: string) {
    if (projectId === activeProjectId || !confirmDiscard()) return;
    await loadProject(projectId);
  }

  async function saveProjectDetails() {
    if (!project || !projectDraft.name.trim()) {
      setError("Project name cannot be empty.");
      return;
    }
    setSavingDetails(true);
    setError(null);
    try {
      const saved = await api.updateProject(project.id, {
        name: projectDraft.name.trim(),
        description: projectDraft.description,
      });
      setProject(saved);
      setProjectDraft({ name: saved.name, description: saved.description });
      setProjects((current) => current.map((item) => (item.id === saved.id ? saved : item)).sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
      setNotice("Project details saved");
      await refreshActivity(saved.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSavingDetails(false);
    }
  }

  async function saveRequirements() {
    if (!project) return;
    setSavingRequirements(true);
    setError(null);
    try {
      const saved = await api.updateRequirements(project.id, requirementsDraft);
      setRequirementsDraft(saved.content);
      setProject((current) => current ? { ...current, requirements: saved.content, updated_at: saved.updated_at } : current);
      setProjects((current) => current.map((item) => item.id === project.id ? { ...item, updated_at: saved.updated_at } : item).sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
      setNotice("Requirements saved");
      await refreshActivity(project.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSavingRequirements(false);
    }
  }

  async function refreshActivity(projectId: string) {
    try {
      setEvents(await api.activity(projectId));
    } catch {
      // The mutation already succeeded; leave the saved state visible if a refresh is unavailable.
    }
  }

  async function selectFile(file: ProjectFile) {
    if (file.id === activeFile?.id || !confirmDiscard()) return;
    setLoadingFile(true);
    setError(null);
    try {
      const document = await api.getFile(activeProjectId || "", file.id);
      setActiveFile(file);
      setFileContent(document.content);
      setFileDraft(document.content);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingFile(false);
    }
  }

  async function saveFile() {
    if (!project || !activeFile) return;
    setSavingFile(true);
    setError(null);
    try {
      const saved = await api.updateFile(project.id, activeFile.id, { content: fileDraft });
      setActiveFile(saved);
      setFiles((current) => current.map((item) => item.id === saved.id ? saved : item));
      setFileContent(saved.content);
      setFileDraft(saved.content);
      setNotice(`${saved.path} saved`);
      await refreshActivity(project.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSavingFile(false);
    }
  }

  async function createProject() {
    if (!newProject.name.trim()) {
      setModalError("Give the project a name to continue.");
      return;
    }
    setModalError(null);
    try {
      const created = await api.createProject({ ...newProject, name: newProject.name.trim() });
      setProjects((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setModal(null);
      setNewProject({ name: "", description: "", requirements: "" });
      await loadProject(created.id);
      setNotice("Project created");
    } catch (caught) {
      setModalError(errorMessage(caught));
    }
  }

  async function createFile() {
    if (!project || !newFilePath.trim()) {
      setModalError("Enter a file path such as rtl/top.sv.");
      return;
    }
    setModalError(null);
    try {
      const created = await api.createFile(project.id, newFilePath.trim(), "");
      setFiles((current) => [...current, created].sort((a, b) => a.path.localeCompare(b.path)));
      setActiveFile(created);
      setFileContent("");
      setFileDraft("");
      setNewFilePath("");
      setModal(null);
      setNotice(`${created.path} created`);
      await refreshActivity(project.id);
    } catch (caught) {
      setModalError(errorMessage(caught));
    }
  }

  async function renameFile() {
    if (!project || !activeFile || !renamePath.trim()) {
      setModalError("Enter the new file path.");
      return;
    }
    setModalError(null);
    try {
      const saved = await api.updateFile(project.id, activeFile.id, { path: renamePath.trim() });
      setActiveFile(saved);
      setFiles((current) => current.map((item) => item.id === saved.id ? saved : item).sort((a, b) => a.path.localeCompare(b.path)));
      setRenamePath("");
      setModal(null);
      setNotice(`${saved.path} renamed`);
      await refreshActivity(project.id);
    } catch (caught) {
      setModalError(errorMessage(caught));
    }
  }

  async function deleteFile() {
    if (!project || !activeFile || !window.confirm(`Delete ${activeFile.path}? This cannot be undone.`)) return;
    setError(null);
    try {
      await api.deleteFile(project.id, activeFile.id);
      const remaining = files.filter((item) => item.id !== activeFile.id);
      setFiles(remaining);
      setActiveFile(null);
      setFileContent("");
      setFileDraft("");
      if (remaining[0]) await selectFile(remaining[0]);
      setNotice(`${activeFile.path} deleted`);
      await refreshActivity(project.id);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function deleteProject() {
    if (!project || !window.confirm(`Delete “${project.name}” and all of its files? This cannot be undone.`)) return;
    setError(null);
    try {
      await api.deleteProject(project.id);
      const remaining = projects.filter((item) => item.id !== project.id);
      setProjects(remaining);
      if (remaining[0]) await loadProject(remaining[0].id);
      else {
        setProject(null);
        setActiveProjectId(null);
        window.localStorage.removeItem("circuitpilot.activeProject");
      }
      setNotice("Project deleted");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function openProjectModal() {
    setModalError(null);
    setNewProject({ name: "", description: "", requirements: "" });
    setModal("project");
  }

  function closeModal() {
    setModal(null);
    setModalError(null);
  }

  if (loading) {
    return <div className="loading-shell"><div className="spinner" aria-label="Loading CircuitPilot" /></div>;
  }

  return (
    <main className="app-shell">
      <div className="app-grid">
        <aside className="sidebar" aria-label="Project navigation">
          <div className="brand">
            <div className="brand-mark"><GitBranch size={18} strokeWidth={2.5} /></div>
            <div><div className="brand-name">CircuitPilot</div><div className="brand-subtitle">Hardware workspace</div></div>
          </div>
          <button className="button primary new-project-button" onClick={openProjectModal}><Plus size={15} /> New project</button>
          <div className="sidebar-label">Saved projects</div>
          <div className="project-list">
            {projects.length === 0 ? (
              <div className="empty-copy" style={{ padding: "10px", color: "#6f8992" }}>No projects yet. Start with a new workspace.</div>
            ) : projects.map((item) => (
              <button key={item.id} className={`project-item ${item.id === activeProjectId ? "active" : ""}`} onClick={() => void navigateProject(item.id)}>
                <FolderOpen size={15} className="project-icon" />
                <span className="project-copy"><span className="project-name">{item.name}</span><span className="project-time">{relativeDate(item.updated_at)}</span></span>
                {item.id === activeProjectId && <ChevronRight size={14} color="#70d8cb" />}
              </button>
            ))}
          </div>
          <div className="sidebar-footer">Local workspace · SQLite + filesystem<br />Phase 1 foundation</div>
        </aside>

        <section className="workspace">
          <div className="workspace-inner">
            {error && <div className="alert" role="alert"><AlertCircle size={15} /> <span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError(null)}><X size={14} /></button></div>}
            {!project ? (
              <div className="empty-state" style={{ minHeight: "80vh" }}>
                <div className="empty-state-inner">
                  <div className="empty-icon"><Boxes size={21} /></div>
                  <div className="empty-title">Your circuit workspaces live here</div>
                  <div className="empty-copy">Create a project to keep requirements, HDL source files, and real user activity together on this machine.</div>
                  <button className="button primary" style={{ marginTop: 18 }} onClick={openProjectModal}><Plus size={15} /> Create your first project</button>
                </div>
              </div>
            ) : (
              <>
                <header className="topbar">
                  <div className="project-heading">
                    <div className="eyebrow">Engineering workspace</div>
                    <input className="project-title-input" aria-label="Project name" value={projectDraft.name} onChange={(event) => setProjectDraft((current) => ({ ...current, name: event.target.value }))} />
                    <input className="project-description-input" aria-label="Project description" placeholder="Add a short project description" value={projectDraft.description} onChange={(event) => setProjectDraft((current) => ({ ...current, description: event.target.value }))} />
                  </div>
                  <div className="topbar-actions">
                    <div className={`save-state ${hasUnsaved ? "unsaved" : ""}`}><span className="save-dot" />{hasUnsaved ? "Unsaved changes" : "All changes saved"}</div>
                    {detailsDirty && <button className="button" onClick={() => void saveProjectDetails()} disabled={savingDetails}><Save size={14} /> {savingDetails ? "Saving…" : "Save details"}</button>}
                    <button className="button" title="Agent execution is not connected in Phase 1" disabled><Sparkles size={14} /> Build circuit</button>
                    <button className="button danger" onClick={() => void deleteProject()}><Trash2 size={14} /> Delete</button>
                  </div>
                </header>

                <section className="requirements-card" aria-labelledby="requirements-heading">
                  <div className="card-heading">
                    <div><div className="section-title" id="requirements-heading">Circuit requirements</div><div className="section-meta">Plain-language specification · saved to this project</div></div>
                    <button className="button primary" onClick={() => void saveRequirements()} disabled={!requirementsDirty || savingRequirements}><Save size={14} /> {savingRequirements ? "Saving…" : "Save requirements"}</button>
                  </div>
                  <textarea className="requirements-textarea" aria-label="Circuit requirements" placeholder="Describe the behavior, interfaces, timing, reset, and edge cases for your circuit…" value={requirementsDraft} onChange={(event) => setRequirementsDraft(event.target.value)} />
                  {!requirementsDraft && <div className="empty-requirements"><CircleHelp size={13} /><span>Need a starting point?</span><button className="hint-link" onClick={() => setRequirementsDraft(EXAMPLE_REQUIREMENTS)}>Insert example requirements</button></div>}
                  <div className="requirements-footer"><span>{requirementsDirty ? "Changes are local until you save." : "Saved requirements are ready for the next phase."}</span><span>{requirementsDraft.length.toLocaleString()} characters</span></div>
                </section>

                <section className="tabs-shell" aria-label="Project workspace">
                  <div className="tabs" role="tablist">
                    <button className={`tab ${tab === "files" ? "active" : ""}`} role="tab" aria-selected={tab === "files"} onClick={() => setTab("files")}><Code2 size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} /> Files</button>
                    <button className={`tab ${tab === "activity" ? "active" : ""}`} role="tab" aria-selected={tab === "activity"} onClick={() => setTab("activity")}><History size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} /> Activity</button>
                    <button className={`tab ${tab === "results" ? "active" : ""}`} role="tab" aria-selected={tab === "results"} onClick={() => setTab("results")}><Layers3 size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} /> Results</button>
                  </div>

                  <div className="tab-content">
                    {tab === "files" && <div className="files-layout">
                      <div className="file-tree" aria-label="Project files">
                        <div className="file-tree-heading"><span>Source files</span><button className="icon-button" aria-label="Create file" title="Create file" onClick={() => { setModalError(null); setModal("file"); }}><FilePlus2 size={15} /></button></div>
                        {files.length === 0 ? <div className="empty-copy" style={{ padding: "8px 5px" }}>No source files yet.<br /><button className="hint-link" style={{ marginTop: 9 }} onClick={() => setModal("file")}>Create a file</button></div> : files.map((file) => (
                          <button key={file.id} className={`file-row ${file.id === activeFile?.id ? "active" : ""}`} onClick={() => void selectFile(file)}>
                            <FileCode2 size={14} /><span className="file-name">{file.path}</span>{file.id === activeFile?.id && fileDirty && <span className="dirty-mark" title="Unsaved changes" />}
                          </button>
                        ))}
                      </div>
                      <div className="editor-pane">
                        <div className="editor-toolbar">
                          <div className="editor-file">{activeFile ? <><FileCode2 size={15} color="#5dc9c0" /><span className="editor-file-name">{activeFile.path}</span>{fileDirty && <span style={{ color: "#fbbf24", fontSize: 11 }}>· unsaved</span>}</> : <span>No file selected</span>}</div>
                          {activeFile && <div className="editor-actions"><button className="icon-button" aria-label="Rename file" title="Rename file" onClick={() => { setRenamePath(activeFile.path); setModalError(null); setModal("rename"); }}><MoreHorizontal size={15} /></button><button className="button primary" onClick={() => void saveFile()} disabled={!fileDirty || savingFile || loadingFile}><Save size={14} /> {savingFile ? "Saving…" : "Save file"}</button></div>}
                        </div>
                        {loadingFile ? <div className="empty-state"><div><div className="spinner" style={{ margin: "0 auto 12px" }} /><div className="empty-copy">Loading file…</div></div></div> : activeFile ? <CodeEditor value={fileDraft} onChange={setFileDraft} filePath={activeFile.path} /> : <div className="empty-state"><div className="empty-state-inner"><div className="empty-icon"><FileCode2 size={20} /></div><div className="empty-title">No source file selected</div><div className="empty-copy">Create a text source file for this project. CircuitPilot will keep it on the local project workspace.</div></div></div>}
                        {activeFile && <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 14px", borderTop: "1px solid var(--border-soft)", color: "#607985", fontSize: 10 }}><span>UTF-8 · text only · never executed</span><button className="button ghost danger" onClick={() => void deleteFile()}><Trash2 size={13} /> Delete file</button></div>}
                      </div>
                    </div>}
                    {tab === "activity" && <div className="activity-list">{events.length === 0 ? <div className="empty-state"><div className="empty-state-inner"><div className="empty-icon"><History size={20} /></div><div className="empty-title">No project activity yet</div><div className="empty-copy">Saved project actions will appear here. Agent execution events are intentionally not shown until that capability exists.</div></div></div> : events.map((event) => <div className="activity-event" key={event.id}><span className="activity-dot" /><div><div className="activity-summary">{event.summary}</div><div className="activity-type">{eventLabel(event)}</div></div><span className="activity-time" title={readableDate(event.created_at)}>{relativeDate(event.created_at)}</span></div>)}</div>}
                    {tab === "results" && <div className="results-panel"><div className="build-note"><div className="build-note-title"><Info size={17} color="#5dc9c0" /> Execution is not connected yet</div><p>{results?.message || "Simulation and synthesis results will appear here after the Meta-agent execution phase is connected. Phase 1 stores your real project data without inventing runs or verification results."}</p></div></div>}
                  </div>
                </section>
              </>
            )}
          </div>
        </section>
      </div>

      {modal && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeModal(); }}>
        <div className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
          <div className="modal-heading"><div><div className="modal-title" id="modal-title">{modal === "project" ? "Create project" : modal === "file" ? "Create source file" : "Rename source file"}</div><div className="modal-subtitle">{modal === "project" ? "Set up a persistent workspace for a new circuit." : "Use a supported text path such as rtl/top.sv."}</div></div><button className="icon-button" aria-label="Close dialog" onClick={closeModal}><X size={16} /></button></div>
          <div className="modal-body">
            {modal === "project" ? <>
              <div className="field"><label className="field-label" htmlFor="new-project-name">Project name</label><input id="new-project-name" className="field-input" autoFocus placeholder="e.g. UART controller" value={newProject.name} onChange={(event) => setNewProject((current) => ({ ...current, name: event.target.value }))} /></div>
              <div className="field"><label className="field-label" htmlFor="new-project-description">Description <span style={{ color: "#66818b", fontWeight: 400 }}>(optional)</span></label><input id="new-project-description" className="field-input" placeholder="What are you exploring?" value={newProject.description} onChange={(event) => setNewProject((current) => ({ ...current, description: event.target.value }))} /></div>
              <div className="field"><div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}><label className="field-label" htmlFor="new-project-requirements">Initial requirements <span style={{ color: "#66818b", fontWeight: 400 }}>(optional)</span></label><button className="hint-link" onClick={() => setNewProject((current) => ({ ...current, requirements: EXAMPLE_REQUIREMENTS }))}>Use example</button></div><textarea id="new-project-requirements" className="field-textarea" placeholder="Describe the circuit in your own words…" value={newProject.requirements} onChange={(event) => setNewProject((current) => ({ ...current, requirements: event.target.value }))} /><div className="field-help">Requirements are saved exactly as written. No files or agent activity are created automatically.</div></div>
            </> : <div className="field"><label className="field-label" htmlFor="file-path">File path</label><input id="file-path" className="field-input" autoFocus placeholder="rtl/top.sv" value={modal === "file" ? newFilePath : renamePath} onChange={(event) => modal === "file" ? setNewFilePath(event.target.value) : setRenamePath(event.target.value)} /><div className="field-help">Allowed extensions: .v, .sv, .vh, .svh, .md, .txt, .json. Paths stay inside this project workspace.</div></div>}
            {modalError && <div className="inline-error" role="alert">{modalError}</div>}
            <div className="modal-footer"><button className="button" onClick={closeModal}>Cancel</button><button className="button primary" onClick={() => void (modal === "project" ? createProject() : modal === "file" ? createFile() : renameFile())}>{modal === "project" ? <><Plus size={14} /> Create project</> : modal === "file" ? <><FilePlus2 size={14} /> Create file</> : <><Check size={14} /> Rename file</>}</button></div>
          </div>
        </div>
      </div>}
    </main>
  );
}
