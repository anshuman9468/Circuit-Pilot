"use client";

import { AlertTriangle, Check, Download, RefreshCw, Square, Terminal } from "lucide-react";
import { useEffect, useState } from "react";

import { api, ExecutionManifest, ExecutionRun, ToolchainStatus } from "../lib/api";

type Props = { projectId: string; onActivityChanged?: () => void };

const activeStates = new Set(["queued", "running"]);

function stageLabel(stage: string, status: string): string {
  if (stage === "lint" && status === "completed") return "Lint completed";
  if (stage === "compilation" && status === "failed") return "Compilation failed";
  if (stage === "simulation" && status === "timed_out") return "Simulation timed out";
  if (stage === "synthesis" && status === "completed") return "Generic synthesis completed";
  return `${stage[0]?.toUpperCase() || ""}${stage.slice(1)} ${status}`;
}

function runLabel(run: ExecutionRun): string {
  if (run.status === "completed") return run.stages.find((stage) => stage.testbench_report?.status === "passed") ? "Completed · testbench-reported checks" : "Completed · inspect evidence";
  if (run.status === "timed_out") return "Simulation timed out";
  if (run.status === "failed") return "Execution failed";
  if (run.status === "cancelled") return "Execution cancelled";
  if (run.status === "interrupted") return "Execution interrupted by backend restart";
  return run.status === "queued" ? "Queued" : "Running";
}

export default function ExecutionPanel({ projectId, onActivityChanged }: Props) {
  const [toolchain, setToolchain] = useState<ToolchainStatus | null>(null);
  const [manifest, setManifest] = useState<ExecutionManifest | null>(null);
  const [runs, setRuns] = useState<ExecutionRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [topModule, setTopModule] = useState("");
  const [testbenchTop, setTestbenchTop] = useState("");
  const [selectedRtl, setSelectedRtl] = useState<string[]>([]);
  const [selectedTb, setSelectedTb] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedRun = runs.find((run) => run.id === selectedRunId) || runs[0] || null;
  const activeRun = runs.find((run) => activeStates.has(run.status));
  const ready = Boolean(toolchain?.runtime_available && toolchain.image_available && Object.keys(toolchain.tool_versions).length >= 4);
  const canRun = ready && Boolean(manifest?.rtl_paths.length) && Boolean(topModule.trim()) && !activeRun;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [status, nextManifest, nextRuns] = await Promise.all([api.toolchainStatus(), api.executionManifest(projectId), api.listExecutionRuns(projectId)]);
      setToolchain(status);
      setManifest(nextManifest);
      setRuns(nextRuns);
      if (!topModule && nextManifest.top_candidates[0]) setTopModule(nextManifest.top_candidates[0]);
      if (!testbenchTop && nextManifest.testbench_top_candidates[0]) setTestbenchTop(nextManifest.testbench_top_candidates[0]);
      setSelectedRtl((current) => current.length ? current.filter((path) => nextManifest.rtl_paths.includes(path)) : nextManifest.rtl_paths);
      setSelectedTb((current) => current.length ? current.filter((path) => nextManifest.testbench_paths.includes(path)) : nextManifest.testbench_paths);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load execution status.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [projectId]);

  useEffect(() => {
    if (!activeRun) return;
    const timer = window.setInterval(() => {
      void api.getExecutionRun(projectId, activeRun.id).then((next) => {
        setRuns((current) => [next, ...current.filter((item) => item.id !== next.id)]);
        if (!activeStates.has(next.status)) onActivityChanged?.();
      }).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [projectId, activeRun?.id, activeRun?.status, onActivityChanged]);

  async function start(stage: "lint" | "simulation" | "synthesis" | "checks") {
    if (!manifest || !canRun) return;
    if ((stage === "simulation" || stage === "checks") && (!selectedTb.length || !testbenchTop.trim())) {
      setError("Simulation and Run checks require a testbench file and testbench top module.");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const next = await api.startExecution(projectId, {
        stage,
        rtl_paths: selectedRtl,
        testbench_paths: stage === "lint" || stage === "synthesis" ? [] : selectedTb,
        include_dirs: manifest.include_dirs,
        top_module: topModule.trim(),
        testbench_top: stage === "lint" || stage === "synthesis" ? null : testbenchTop.trim(),
      });
      setRuns((current) => [next, ...current.filter((item) => item.id !== next.id)]);
      setSelectedRunId(next.id);
      onActivityChanged?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The execution could not be started.");
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!activeRun) return;
    try {
      const next = await api.cancelExecution(projectId, activeRun.id);
      setRuns((current) => [next, ...current.filter((item) => item.id !== next.id)]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The execution could not be cancelled.");
    }
  }

  const blockingMessage = !toolchain ? "Checking container readiness…" : !toolchain.runtime_available ? "Install Podman or Docker; host execution is disabled." : !toolchain.image_available ? "Build the configured toolchain image explicitly." : Object.keys(toolchain.tool_versions).length < 4 ? "The image must contain Verilator, Icarus Verilog/vvp, and Yosys." : !manifest?.rtl_paths.length ? "Create at least one RTL source file." : !topModule ? "Choose a top module." : activeRun ? "An execution is already running for this project." : "";

  return <div className="execution-panel">
    <div className="execution-heading">
      <div><div className="section-title">Engineering tool execution</div><div className="section-meta">Runs immutable source snapshots in a constrained container. No model calls are made.</div></div>
      <button className="button" onClick={() => void load()} disabled={loading}><RefreshCw size={13} /> Refresh</button>
    </div>
    {error && <div className="alert"><AlertTriangle size={15} /><span>{error}</span></div>}
    <div className="toolchain-card">
      <div className="toolchain-card-heading"><div><strong>Toolchain readiness</strong><div className="section-meta">Status checks never build or download images.</div></div><span className={`execution-badge ${ready ? "ready" : "blocked"}`}>{ready ? "READY" : "NOT READY"}</span></div>
      {loading ? <div className="execution-muted"><span className="spinner" /> Checking runtime…</div> : <><div className="toolchain-summary"><span>Runtime: {toolchain?.runtime || "missing"}</span><span>Image: {toolchain?.image || "—"}</span><span>Tools: {Object.keys(toolchain?.tool_versions || {}).join(", ") || "none detected"}</span></div>{!ready && <div className="execution-warning"><AlertTriangle size={14} /><div><strong>{toolchain?.message || blockingMessage}</strong>{toolchain?.setup_instructions.map((instruction) => <div key={instruction}>{instruction}</div>)}</div></div>}</>}
    </div>
    <div className="execution-card">
      <div className="execution-card-heading"><div><strong>Execution manifest</strong><div className="section-meta">Synthesis uses RTL only. Simulation uses RTL plus the selected testbench.</div></div></div>
      <div className="execution-form-grid"><label className="field"><span className="field-label">RTL top module</span><input className="field-input" value={topModule} onChange={(event) => setTopModule(event.target.value)} placeholder="top_module" /></label><label className="field"><span className="field-label">Testbench top module</span><input className="field-input" value={testbenchTop} onChange={(event) => setTestbenchTop(event.target.value)} placeholder="tb_top" /></label></div>
      <div className="manifest-columns"><div><div className="field-label">RTL sources</div>{manifest?.rtl_paths.map((path) => <label className="manifest-file" key={path}><input type="checkbox" checked={selectedRtl.includes(path)} onChange={(event) => setSelectedRtl((current) => event.target.checked ? [...current, path] : current.filter((item) => item !== path))} /> <span>{path}</span></label>) || <div className="execution-muted">No HDL files found.</div>}</div><div><div className="field-label">Testbench sources</div>{manifest?.testbench_paths.map((path) => <label className="manifest-file" key={path}><input type="checkbox" checked={selectedTb.includes(path)} onChange={(event) => setSelectedTb((current) => event.target.checked ? [...current, path] : current.filter((item) => item !== path))} /> <span>{path}</span></label>) || <div className="execution-muted">No testbench files found.</div>}</div></div>
      <div className="execution-actions"><button className="button" disabled={!canRun || starting} onClick={() => void start("lint")}>Run lint</button><button className="button" disabled={!canRun || starting || !selectedTb.length} onClick={() => void start("simulation")}>Run simulation</button><button className="button" disabled={!canRun || starting} onClick={() => void start("synthesis")}>Run synthesis</button><button className="button primary" disabled={!canRun || starting || !selectedTb.length} onClick={() => void start("checks")}>Run checks</button>{activeRun && <button className="button danger" onClick={() => void cancel()}><Square size={12} fill="currentColor" /> Cancel</button>}</div>
      {!canRun && <div className="field-help">{blockingMessage}</div>}
    </div>
    <div className="execution-card"><div className="execution-card-heading"><div><strong>Persistent run history</strong><div className="section-meta">Results belong to the captured snapshot and remain available after source edits.</div></div></div>{runs.length === 0 ? <div className="empty-state"><div className="empty-state-inner"><Terminal size={20} color="#5dc9c0" /><div className="empty-title">No engineering runs yet</div><div className="empty-copy">Run a stage when the container image is ready. Missing tools are never replaced with fabricated results.</div></div></div> : <div className="run-history">{runs.map((run) => <button className={`execution-run-row ${selectedRun?.id === run.id ? "active" : ""}`} key={run.id} onClick={() => setSelectedRunId(run.id)}><span className={`status-dot execution-status-${run.status}`} /><span><strong>{run.stage}</strong><small>{runLabel(run)}</small></span><time>{new Date(run.created_at).toLocaleString()}</time></button>)}</div>}</div>
    {selectedRun && <div className="execution-card execution-detail"><div className="execution-card-heading"><div><strong>{runLabel(selectedRun)}</strong><div className="section-meta">Snapshot {selectedRun.snapshot_id || "pending"} · {selectedRun.specification_revision ? `specification revision ${selectedRun.specification_revision}` : "no specification revision selected"}</div></div><span className={`execution-badge ${activeStates.has(selectedRun.status) ? "running" : selectedRun.status === "completed" ? "ready" : "blocked"}`}>{selectedRun.status.toUpperCase()}</span></div>{selectedRun.error_message && <div className="execution-warning"><AlertTriangle size={14} /><span>{selectedRun.error_message}</span></div>}<div className="stage-list">{selectedRun.stages.map((stage) => <div className="stage-row" key={`${selectedRun.id}-${stage.stage}`}><div><strong>{stageLabel(stage.stage, stage.status)}</strong><small>{stage.tool}{stage.tool_version ? ` · ${stage.tool_version}` : ""}{stage.log_truncated ? " · log truncated" : ""}</small></div><span>{stage.status}</span>{stage.testbench_report && <small>{stage.testbench_report.status === "passed" ? `Testbench reports ${stage.testbench_report.checks_passed} checks passed` : stage.testbench_report.status === "unknown" ? "Simulation completed; verification outcome unknown" : `${stage.testbench_report.checks_failed} checks failed`}</small>}{stage.diagnostics.slice(0, 3).map((diagnostic, index) => <div className={`diagnostic ${diagnostic.severity}`} key={`${stage.stage}-diag-${index}`}>{diagnostic.file ? `${diagnostic.file}:${diagnostic.line || "?"} ` : ""}{diagnostic.message}</div>)}{stage.artifact_ids.map((artifactId) => { const artifact = selectedRun.artifacts.find((item) => item.id === artifactId); return <a className="artifact-link" key={artifactId} href={api.executionArtifactUrl(projectId, selectedRun.id, artifactId)} download><Download size={12} /> {artifact?.name || "Download artifact"}</a>; })}</div>)}</div><div className="execution-events">{selectedRun.events.slice(-8).map((event) => <div key={event.id}>· {event.summary}</div>)}</div></div>}
  </div>;
}
