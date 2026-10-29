"use client";

import { AlertTriangle, Check, ClipboardList, LoaderCircle, Play, Square, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { CircuitSpecification, SpecificationPlannerRun, SpecificationResponse, api } from "../lib/api";

type Props = {
  projectId: string;
  specification: CircuitSpecification;
  validation: SpecificationResponse["validation"] | null;
  currentRevision: number;
  dirty: boolean;
  onActivityChanged: () => void;
  onAccepted: () => Promise<void>;
};

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "The model specification planning request could not be completed.";
}

function terminal(state: string): boolean {
  return !["queued", "running"].includes(state);
}

function stateLabel(state: string): string {
  return {
    queued: "Queued",
    running: "Drafting specification",
    proposed: "Proposal ready",
    accepted: "Accepted as draft",
    discarded: "Discarded",
    failed: "Planning failed",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
  }[state] || state;
}

export default function SpecificationPlannerPanel({ projectId, specification, validation, currentRevision, dirty, onActivityChanged, onAccepted }: Props) {
  const [config, setConfig] = useState<Awaited<ReturnType<typeof api.specificationPlannerConfig>> | null>(null);
  const [run, setRun] = useState<SpecificationPlannerRun | null>(null);
  const [instruction, setInstruction] = useState("Create a complete structured specification from the saved request. Preserve known user fields, ask questions instead of inventing missing values, and include a module and verification plan.");
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    Promise.all([api.specificationPlannerConfig(), api.listSpecificationPlannerRuns(projectId)])
      .then(([loadedConfig, runs]) => {
        if (!mounted) return;
        setConfig(loadedConfig);
        const latest = runs[0];
        if (latest) return api.getSpecificationPlannerRun(projectId, latest.id).then((loadedRun) => { if (mounted) setRun(loadedRun); });
      })
      .catch((caught) => { if (mounted) setError(messageFor(caught)); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [projectId]);

  useEffect(() => {
    if (!run || terminal(run.state)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const current = await api.getSpecificationPlannerRun(projectId, run.id);
        if (stopped) return;
        setRun(current);
        if (!terminal(current.state)) timer = setTimeout(() => void poll(), 800);
      } catch (caught) {
        if (!stopped) setError(messageFor(caught));
      }
    };
    void poll();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [projectId, run?.id, run?.state]);

  const providerName = config?.provider || "Model";
  const blockedReason = !config
    ? "Checking model planner setup…"
    : !config.enabled
      ? config.message
      : !config.configured
        ? config.message
        : dirty
          ? `Save the current structured specification before asking ${providerName} to plan from it.`
          : currentRevision !== specification.revision
            ? "Open the current specification revision before planning."
            : null;
  const active = run && !terminal(run.state);
  const proposal = run?.proposal;

  async function start() {
    if (blockedReason || starting || active) return;
    setStarting(true); setError(null); setNotice(null);
    try {
      const created = await api.startSpecificationPlanner(projectId, currentRevision, instruction);
      setRun(created);
      setNotice(`${providerName} planning started. The saved specification will not change until you accept the proposal.`);
      onActivityChanged();
    } catch (caught) { setError(messageFor(caught)); }
    finally { setStarting(false); }
  }

  async function cancel() {
    if (!run) return;
    setActing(true); setError(null);
    try { setRun(await api.cancelSpecificationPlannerRun(projectId, run.id)); setNotice("Planning cancellation requested."); onActivityChanged(); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setActing(false); }
  }

  async function accept() {
    if (!run || !proposal) return;
    setActing(true); setError(null); setNotice(null);
    try {
      const accepted = await api.acceptSpecificationPlannerRun(projectId, run.id, run.base_revision);
      setRun(accepted);
      setNotice(`${providerName} proposal accepted as draft revision ${accepted.accepted_revision ?? ""}. Review validation before marking it ready.`);
      onActivityChanged();
      await onAccepted();
    } catch (caught) { setError(messageFor(caught)); }
    finally { setActing(false); }
  }

  async function discard() {
    if (!run || !window.confirm(`Discard this ${providerName} specification proposal? The saved specification will not change.`)) return;
    setActing(true); setError(null); setNotice(null);
    try { setRun(await api.discardSpecificationPlannerRun(projectId, run.id)); setNotice(`${providerName} proposal discarded without changing the saved specification.`); onActivityChanged(); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setActing(false); }
  }

  return <section className="planner-panel" aria-labelledby="planner-heading">
    <div className="generation-heading">
      <div>
        <div className="section-title" id="planner-heading">Draft specification with {providerName}</div>
        <div className="section-meta">{providerName} proposes structured fields, clarifications, modules, and verification objectives. Nothing is saved until you accept it.</div>
      </div>
      <div className="generation-actions">
        <span className={`generation-status ${config?.status || "loading"}`}>{config?.status === "disabled" ? "Disabled" : config?.status === "not_configured" ? "Not configured" : config?.status === "configured" ? "Enabled" : "Checking setup…"}</span>
        <button className="button primary" onClick={() => void start()} disabled={Boolean(blockedReason) || Boolean(active) || starting}><Play size={13} /> {starting ? "Starting…" : active ? "Planning active" : `Draft with ${providerName}`}</button>
      </div>
    </div>
    {blockedReason && <div className="generation-note"><AlertTriangle size={14} /><span>{blockedReason}</span></div>}
    <div className="planner-instruction">
      <label className="field"><span className="field-label">Planner instruction</span><textarea className="field-textarea" value={instruction} onChange={(event) => setInstruction(event.target.value)} disabled={Boolean(active)} /></label>
      <div className="field-help">The planner receives the saved request, current specification, project description, and validation findings. Keep secrets and provider credentials out of this field.</div>
    </div>
    {loading ? <div className="generation-loading"><LoaderCircle size={15} className="spin-icon" /> Loading planner state…</div> : <>
      {error && <div className="inline-error" role="alert"><AlertTriangle size={13} /> {error}<button className="icon-button" aria-label="Dismiss planner error" onClick={() => setError(null)}><X size={13} /></button></div>}
      {notice && <div className="inline-success" role="status"><Check size={13} /> {notice}<button className="icon-button" aria-label="Dismiss planner notice" onClick={() => setNotice(null)}><X size={13} /></button></div>}
      {run && <div className="generation-run-card"><div className="generation-run-topline"><span className={`generation-state ${run.state}`}>{run.state === "running" && <LoaderCircle size={13} className="spin-icon" />}{stateLabel(run.state)}</span><span>Base specification revision {run.base_revision}</span>{active && <button className="button danger" onClick={() => void cancel()} disabled={acting}><Square size={11} fill="currentColor" /> Cancel</button>}</div><div className="generation-events">{run.events.slice(-5).map((event) => <div className="generation-event" key={event.id}><span>·</span><span>{event.summary}</span></div>)}</div>{run.total_tokens !== null && <div className="field-help">Provider-reported usage · {run.prompt_tokens ?? "—"} prompt / {run.completion_tokens ?? "—"} completion / {run.total_tokens} total tokens</div>}{run.error_message && <div className="generation-error">{run.error_message}</div>}</div>}
      {proposal && <div className="planner-proposal"><div className="proposal-review-heading"><div><div className="section-title"><ClipboardList size={14} /> Proposed structured specification · {run?.state}</div><div className="section-meta">Based on saved revision {run?.base_revision} · proposal from {run?.model}</div></div><div className="proposal-review-actions">{run?.state === "proposed" && <><button className="button danger" onClick={() => void discard()} disabled={acting}><Trash2 size={13} /> Discard</button><button className="button primary" onClick={() => void accept()} disabled={acting}><Check size={13} /> Accept as draft</button></>}</div></div><p className="proposal-summary">{proposal.summary || "Meta returned a structured specification proposal for review."}</p><div className="planner-stat-grid"><span><strong>{proposal.proposed_specification.parameters.length}</strong> parameters</span><span><strong>{proposal.proposed_specification.ports.length}</strong> ports</span><span><strong>{proposal.proposed_specification.functional_requirements.length}</strong> requirements</span><span><strong>{proposal.proposed_specification.modules.length}</strong> modules</span><span><strong>{proposal.proposed_specification.verification_objectives.length}</strong> verification objectives</span></div>{run?.validation && <div className={`spec-validation ${run.validation.valid ? "valid" : "invalid"}`}><div className="spec-validation-heading"><span>{run.validation.valid ? <Check size={14} /> : <AlertTriangle size={14} />}</span><strong>{run.validation.valid ? "No blocking structural issues" : `${run.validation.blocking_count} blocking structural issue${run.validation.blocking_count === 1 ? "" : "s"}`}</strong><span className="validation-advisory">{run.validation.advisory_count} advisory</span></div><div className="field-help">The deterministic validator still cannot prove that the proposal captures all intended circuit semantics.</div></div>} {(proposal.assumptions.length > 0 || proposal.clarification_questions.length > 0) && <div className="proposal-meta-grid">{proposal.assumptions.length > 0 && <div><strong>Assumptions to review</strong>{proposal.assumptions.map((item) => <span key={item.id}>• {item.text}{item.accepted ? "" : " (not accepted)"}</span>)}</div>}{proposal.clarification_questions.length > 0 && <div><strong>Clarification questions</strong>{proposal.clarification_questions.map((item) => <span key={item.id}>• {item.question}</span>)}</div>}</div>}<details className="planner-json"><summary>Inspect proposed specification JSON</summary><pre>{JSON.stringify(proposal.proposed_specification, null, 2)}</pre></details><div className="honest-status-row"><span>Structured proposal only</span><span>Not RTL</span><span>Not verified</span></div></div>}
    </>}
  </section>;
}
