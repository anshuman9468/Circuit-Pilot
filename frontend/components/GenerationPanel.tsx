"use client";

import { AlertTriangle, Check, FileCode2, GitCompare, LoaderCircle, Play, Square, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { CircuitSpecification, GenerationJob, GenerationProposal, ProjectFile, SpecificationResponse, api } from "../lib/api";

type Props = {
  projectId: string;
  specification: CircuitSpecification;
  validation: SpecificationResponse["validation"] | null;
  status: "draft" | "ready";
  currentRevision: number;
  onActivityChanged: () => void;
  onFilesChanged: () => Promise<void>;
};

function messageFor(error: unknown): string { return error instanceof Error ? error.message : "The generation request could not be completed."; }
function terminal(state: string): boolean { return !["queued", "running"].includes(state); }
function stateLabel(state: string): string {
  return { queued: "Queued", running: "Generating proposal", awaiting_clarification: "Awaiting clarification", proposed: "Proposal ready", applied: "Applied", discarded: "Discarded", failed: "Generation failed", cancelled: "Cancelled", interrupted: "Interrupted" }[state] || state;
}

export default function GenerationPanel({ projectId, specification, validation, status, currentRevision, onActivityChanged, onFilesChanged }: Props) {
  const [config, setConfig] = useState<Awaited<ReturnType<typeof api.generationConfig>> | null>(null);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [proposal, setProposal] = useState<GenerationProposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [acting, setActing] = useState(false);
  const [includeContext, setIncludeContext] = useState(false);
  const [contextFileIds, setContextFileIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    Promise.all([api.generationConfig(), api.listFiles(projectId), api.listGenerations(projectId), api.listGenerationProposals(projectId)])
      .then(([loadedConfig, loadedFiles, generations, proposals]) => {
        if (!mounted) return;
        setConfig(loadedConfig); setFiles(loadedFiles);
        const active = generations.find((item) => !terminal(item.state));
        setJob(active || generations[0] || null);
        const proposalId = active?.proposal_id || generations[0]?.proposal_id || proposals[0]?.proposal_id;
        if (proposalId) return api.getGenerationProposal(projectId, proposalId).then((loadedProposal) => { if (mounted) setProposal(loadedProposal); });
      })
      .catch((caught) => { if (mounted) setError(messageFor(caught)); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [projectId]);

  useEffect(() => {
    if (!job || terminal(job.state)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const current = await api.getGeneration(projectId, job.id);
        if (stopped) return;
        setJob(current);
        if (!terminal(current.state)) timer = setTimeout(() => void poll(), 900);
        else if (current.proposal_id) setProposal(await api.getGenerationProposal(projectId, current.proposal_id));
      } catch (caught) { if (!stopped) setError(messageFor(caught)); }
    };
    void poll();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [projectId, job?.id, job?.state]);

  const blockedReason = !config ? "Checking generation setup…" : !config.enabled ? config.message : !config.configured ? config.message : status !== "ready" || specification.status !== "ready" ? "Mark a saved specification revision ready before generating." : !validation?.valid ? "Resolve blocking specification issues before generating." : currentRevision !== specification.revision ? "Open the current specification revision before generating." : null;
  const active = job && !terminal(job.state);

  async function start() {
    if (blockedReason || starting || active) return;
    setStarting(true); setError(null); setNotice(null);
    try {
      const created = await api.startGeneration(projectId, currentRevision, { include_existing_context: includeContext, context_file_ids: includeContext ? contextFileIds : [] });
      setJob(created); setProposal(null); setNotice("Generation started. Files will remain staged until you review them."); onActivityChanged();
    } catch (caught) { setError(messageFor(caught)); }
    finally { setStarting(false); }
  }

  async function cancel() {
    if (!job) return;
    setActing(true); setError(null);
    try { setJob(await api.cancelGeneration(projectId, job.id)); setNotice("Cancellation requested"); onActivityChanged(); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setActing(false); }
  }

  async function accept() {
    if (!proposal) return;
    setActing(true); setError(null);
    try { const saved = await api.acceptGenerationProposal(projectId, proposal.proposal_id); setProposal(saved); setJob((current) => current ? { ...current, state: "applied" } : current); setNotice("Proposal applied. Generated files are present in Files; they have not been compiled or simulated."); onActivityChanged(); await onFilesChanged(); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setActing(false); }
  }

  async function discard() {
    if (!proposal || !window.confirm("Discard this staged proposal? The active project files will not change.")) return;
    setActing(true); setError(null);
    try { const saved = await api.discardGenerationProposal(projectId, proposal.proposal_id); setProposal(saved); setJob((current) => current ? { ...current, state: "discarded" } : current); setNotice("Proposal discarded without changing project files."); onActivityChanged(); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setActing(false); }
  }

  return <section className="generation-panel" aria-labelledby="generation-heading">
    <div className="generation-heading"><div><div className="section-title" id="generation-heading">RTL proposal generation</div><div className="section-meta">Uses only a saved ready revision. Generated files are staged for review and are never executed in Phase 4.</div></div><div className="generation-actions"><span className={`generation-status ${config?.status || "loading"}`}>{config?.status === "disabled" ? "Disabled" : config?.status === "not_configured" ? "Not configured" : config?.status === "configured" ? "Enabled" : "Checking setup…"}</span><button className="button primary" onClick={() => void start()} disabled={Boolean(blockedReason) || Boolean(active) || starting}><Play size={13} /> {starting ? "Starting…" : active ? "Generation active" : "Generate RTL"}</button></div></div>
    {blockedReason && <div className="generation-note"><AlertTriangle size={14} /><span>{blockedReason}</span></div>}
    {loading ? <div className="generation-loading"><LoaderCircle size={15} className="spin-icon" /> Loading generation state…</div> : <>
      {config?.enabled && config.configured && status === "ready" && !active && !proposal && <div className="generation-context"><label className="checkbox-field"><input type="checkbox" checked={includeContext} onChange={(event) => setIncludeContext(event.target.checked)} /> Include selected existing files as revision context</label>{includeContext && <div className="context-file-list">{files.length === 0 ? <span className="field-help">No existing files to include.</span> : files.map((file) => <label className="context-file" key={file.id}><input type="checkbox" checked={contextFileIds.includes(file.id)} onChange={(event) => setContextFileIds((current) => event.target.checked ? [...current, file.id] : current.filter((id) => id !== file.id))} /><FileCode2 size={13} /> <span>{file.path}</span></label>)}</div>}</div>}
      {error && <div className="inline-error" role="alert"><AlertTriangle size={13} /> {error}<button className="icon-button" aria-label="Dismiss generation error" onClick={() => setError(null)}><X size={13} /></button></div>}
      {notice && <div className="inline-success" role="status"><Check size={13} /> {notice}<button className="icon-button" aria-label="Dismiss generation notice" onClick={() => setNotice(null)}><X size={13} /></button></div>}
      {job && <div className="generation-run-card"><div className="generation-run-topline"><span className={`generation-state ${job.state}`}>{job.state === "running" && <LoaderCircle size={13} className="spin-icon" />}{stateLabel(job.state)}</span><span>Specification revision {job.specification_revision}</span>{active && <button className="button danger" onClick={() => void cancel()} disabled={acting}><Square size={11} fill="currentColor" /> Cancel</button>}</div><div className="generation-events">{job.events.slice(-5).map((event) => <div className="generation-event" key={event.id}><span>·</span><span>{event.summary}</span></div>)}</div>{job.total_tokens !== null && <div className="field-help">Provider-reported usage · {job.prompt_tokens ?? "—"} prompt / {job.completion_tokens ?? "—"} completion / {job.total_tokens} total tokens</div>}{job.error_message && <div className="generation-error">{job.error_message}</div>}{job.state === "awaiting_clarification" && job.clarification.map((item) => <div className="generation-clarification" key={item.id}><strong>{item.related_field}</strong><span>{item.reason}</span><small>Resolve this in the structured specification, then start a new generation.</small></div>)}</div>}
      {proposal && <div className="proposal-review"><div className="proposal-review-heading"><div><div className="section-title">Proposed files · {proposal.status}</div><div className="section-meta">Top module <code>{proposal.result.top_module}</code> · testbench <code>{proposal.result.testbench_top}</code> · specification revision {proposal.specification_revision}</div></div><div className="proposal-review-actions">{proposal.status === "proposed" && <><button className="button danger" onClick={() => void discard()} disabled={acting}><Trash2 size={13} /> Discard</button><button className="button primary" onClick={() => void accept()} disabled={acting}><Check size={13} /> Accept proposal</button></>}</div></div><p className="proposal-summary">{proposal.result.implementation_summary}</p><div className="proposal-file-list">{proposal.files.map((file) => <details className="proposal-file" key={file.path}><summary><GitCompare size={13} /><code>{file.path}</code><span className={`diff-label ${file.change_type}`}>{file.change_type}</span></summary><div className="proposal-file-body">{file.existing_content !== null && <div><div className="proposal-code-label">Existing</div><pre>{file.existing_content}</pre></div>}<div><div className="proposal-code-label">Proposed</div><pre>{file.proposed_content}</pre></div></div></details>)}</div>{(proposal.assumptions.length > 0 || proposal.unresolved_questions.length > 0 || proposal.known_limitations.length > 0 || proposal.result.unmapped_requirements.length > 0) && <div className="proposal-meta-grid">{proposal.assumptions.length > 0 && <div><strong>Assumptions</strong>{proposal.assumptions.map((item) => <span key={item}>• {item}</span>)}</div>}{proposal.result.unmapped_requirements.length > 0 && <div><strong>Unmapped requirements</strong>{proposal.result.unmapped_requirements.map((item) => <span key={item}>• {item}</span>)}</div>}{proposal.unresolved_questions.length > 0 && <div><strong>Unresolved questions</strong>{proposal.unresolved_questions.map((item) => <span key={item}>• {item}</span>)}</div>}{proposal.known_limitations.length > 0 && <div><strong>Limitations</strong>{proposal.known_limitations.map((item) => <span key={item}>• {item}</span>)}</div>}</div>}<div className="honest-status-row"><span>Generated — not compiled</span><span>Testbench created — not executed</span><span>Synthesis not run</span></div></div>}
    </>}
  </section>;
}
