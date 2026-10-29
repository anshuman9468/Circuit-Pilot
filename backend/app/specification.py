from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


SPEC_SCHEMA_VERSION = "1.0"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PARAMETER_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EntrySource = Literal["user", "assumption", "planner"]
DesignCategory = Literal["combinational", "sequential", "mixed", "unspecified"]
PortDirection = Literal["input", "output", "inout", "unspecified"]
Signedness = Literal["signed", "unsigned", "unspecified"]
ClockEdge = Literal["rising", "falling", "either", "unspecified"]
ResetPolarity = Literal["active_high", "active_low", "unspecified"]
ResetBehavior = Literal["synchronous", "asynchronous", "unspecified"]
ResetIntent = Literal["required", "not_intended", "unspecified"]
QuestionStatus = Literal["open", "resolved"]
QuestionSeverity = Literal["blocking", "advisory"]
CheckType = Literal["directed", "boundary", "reset", "sequence", "invariant", "unspecified"]
ValidationSeverity = Literal["blocking", "advisory"]
ClarificationStatus = Literal["open", "resolved"]
ExpectedAnswerType = Literal["text", "choice", "positive_integer_or_parameter", "boolean", "object"]
SpecificationStatus = Literal["draft", "ready"]


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _identifier(value: str) -> str:
    value = value.strip()
    if value and not IDENTIFIER.fullmatch(value):
        raise ValueError("must be a Verilog-style identifier")
    return value


def _width(value: int | str | None) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("width must be a positive integer or parameter reference")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("width must be a positive integer")
        return value
    value = value.strip()
    if not PARAMETER_REFERENCE.fullmatch(value):
        raise ValueError("width must be a positive integer or a simple parameter reference")
    return value


class SpecificationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Parameter(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("param"), max_length=80)
    name: str = Field(default="", max_length=120)
    default_value: int | str | None = None
    width: int | str | None = None
    constraints: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)
    source: EntrySource = "user"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("width")
    @classmethod
    def validate_width(cls, value: int | str | None) -> int | str | None:
        return _width(value)


class Port(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("port"), max_length=80)
    name: str = Field(default="", max_length=120)
    direction: PortDirection = "unspecified"
    width: int | str | None = None
    signedness: Signedness = "unspecified"
    purpose: str = Field(default="", max_length=2000)
    source: EntrySource = "user"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("width")
    @classmethod
    def validate_width(cls, value: int | str | None) -> int | str | None:
        return _width(value)


class ClockDomain(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("clock"), max_length=80)
    name: str = Field(default="", max_length=120)
    signal: str = Field(default="", max_length=120)
    frequency_hz: float | None = Field(default=None, gt=0)
    edge: ClockEdge = "unspecified"
    source: EntrySource = "user"

    @field_validator("name", "signal")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return _identifier(value)


class ResetSignal(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("reset"), max_length=80)
    name: str = Field(default="", max_length=120)
    signal: str = Field(default="", max_length=120)
    polarity: ResetPolarity = "unspecified"
    behavior: ResetBehavior = "unspecified"
    associated_clock_domain: str | None = Field(default=None, max_length=120)
    intended_reset_state: int | None = Field(default=None, ge=0, le=1)
    source: EntrySource = "user"

    @field_validator("name", "signal", "associated_clock_domain")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None


class FunctionalRequirement(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("req"), max_length=80)
    title: str = Field(default="", max_length=200)
    statement: str = Field(default="", max_length=4000)
    source: EntrySource = "user"


class TimingExpectations(SpecificationBase):
    latency_cycles: int | None = Field(default=None, ge=0)
    max_latency_cycles: int | None = Field(default=None, ge=0)
    throughput: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=2000)
    source: EntrySource = "user"


class BoundaryBehavior(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("boundary"), max_length=80)
    condition: str = Field(default="", max_length=2000)
    behavior: str = Field(default="", max_length=3000)
    source: EntrySource = "user"


class Assumption(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("assumption"), max_length=80)
    text: str = Field(default="", max_length=2000)
    accepted: bool = False
    source: EntrySource = "assumption"


class OpenQuestion(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("question"), max_length=80)
    question: str = Field(default="", max_length=2000)
    related_field: str = Field(default="", max_length=255)
    severity: QuestionSeverity = "advisory"
    status: QuestionStatus = "open"
    answer: str = Field(default="", max_length=3000)
    source: EntrySource = "user"


class ModuleConnection(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("connection"), max_length=80)
    from_module: str = Field(default="", max_length=120)
    from_port: str = Field(default="", max_length=120)
    to_module: str = Field(default="", max_length=120)
    to_port: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=1000)
    source: EntrySource = "user"

    @field_validator("from_module", "from_port", "to_module", "to_port")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return _identifier(value)


class ModulePlan(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("module"), max_length=80)
    name: str = Field(default="", max_length=120)
    responsibility: str = Field(default="", max_length=3000)
    input_ports: list[str] = Field(default_factory=list, max_length=100)
    output_ports: list[str] = Field(default_factory=list, max_length=100)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    internal_state: str = Field(default="", max_length=2000)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    connections: list[ModuleConnection] = Field(default_factory=list, max_length=100)
    design_notes: str = Field(default="", max_length=3000)
    is_top_level: bool = False
    source: EntrySource = "user"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("input_ports", "output_ports", "requirement_ids", "dependencies")
    @classmethod
    def validate_references(cls, values: list[str]) -> list[str]:
        return [_identifier(value) if "-" not in value else value.strip() for value in values]


class VerificationObjective(SpecificationBase):
    id: str = Field(default_factory=lambda: _id("verify"), max_length=80)
    title: str = Field(default="", max_length=200)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    preconditions: str = Field(default="", max_length=2000)
    stimulus: str = Field(default="", max_length=3000)
    expected_behavior: str = Field(default="", max_length=3000)
    check_type: CheckType = "unspecified"
    notes: str = Field(default="", max_length=2000)
    source: EntrySource = "user"


class CircuitSpecification(SpecificationBase):
    specification_id: str = Field(default="", max_length=80)
    project_id: str = Field(default="", max_length=80)
    schema_version: str = SPEC_SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    status: SpecificationStatus = "draft"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    original_request: str = Field(default="", max_length=20_000)
    circuit_name: str = Field(default="", max_length=200)
    purpose: str = Field(default="", max_length=4000)
    design_category: DesignCategory = "unspecified"
    parameters: list[Parameter] = Field(default_factory=list, max_length=100)
    ports: list[Port] = Field(default_factory=list, max_length=200)
    clock_domains: list[ClockDomain] = Field(default_factory=list, max_length=50)
    reset_intent: ResetIntent = "unspecified"
    reset_signals: list[ResetSignal] = Field(default_factory=list, max_length=50)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list, max_length=200)
    timing: TimingExpectations | None = None
    boundary_behaviors: list[BoundaryBehavior] = Field(default_factory=list, max_length=100)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=100)
    open_questions: list[OpenQuestion] = Field(default_factory=list, max_length=100)
    modules: list[ModulePlan] = Field(default_factory=list, max_length=100)
    verification_objectives: list[VerificationObjective] = Field(default_factory=list, max_length=300)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    related_field: str
    field_path: str
    reason: str
    severity: ValidationSeverity
    expected_answer_type: ExpectedAnswerType
    status: ClarificationStatus = "open"
    code: str


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    blocking_count: int = 0
    advisory_count: int = 0
    generated_at: datetime = Field(default_factory=utc_now)
    findings: list[Clarification] = Field(default_factory=list)
    limitation: str = "Structural checks cannot prove that the specification captures all intended circuit semantics."


def _finding(
    findings: list[Clarification],
    *,
    code: str,
    related_field: str,
    field_path: str,
    reason: str,
    severity: ValidationSeverity,
    expected_answer_type: ExpectedAnswerType,
) -> None:
    findings.append(
        Clarification(
            id=f"{code}:{field_path}",
            related_field=related_field,
            field_path=field_path,
            reason=reason,
            severity=severity,
            expected_answer_type=expected_answer_type,
            code=code,
        )
    )


def _merge_previous(previous: ValidationReport | None, current: list[Clarification]) -> list[Clarification]:
    if previous is None:
        return current
    current_ids = {item.id for item in current}
    resolved = [item.model_copy(update={"status": "resolved"}) for item in previous.findings if item.id not in current_ids]
    return [*current, *resolved]


def _known_width(width: int | str | None, parameters: dict[str, Parameter]) -> int | None:
    """Resolve only literal widths and integer parameter defaults.

    This deliberately does not evaluate expressions. A symbolic parameter with
    no integer default remains unresolved and is reported as such.
    """
    if isinstance(width, int):
        return width
    if isinstance(width, str):
        default = parameters.get(width)
        if default is not None and isinstance(default.default_value, int) and not isinstance(default.default_value, bool):
            return default.default_value
    return None


def validate_specification(
    specification: CircuitSpecification,
    previous: ValidationReport | None = None,
) -> ValidationReport:
    findings: list[Clarification] = []
    if not specification.original_request.strip():
        _finding(findings, code="original_request_missing", related_field="Original request", field_path="original_request", reason="Preserve the natural-language request that this specification is based on.", severity="blocking", expected_answer_type="text")
    if not specification.circuit_name.strip():
        _finding(findings, code="circuit_name_missing", related_field="Circuit name", field_path="circuit_name", reason="Give the design a stable circuit name.", severity="blocking", expected_answer_type="text")
    if not specification.purpose.strip():
        _finding(findings, code="purpose_missing", related_field="Purpose", field_path="purpose", reason="Describe what the circuit is intended to do.", severity="blocking", expected_answer_type="text")
    if specification.design_category == "unspecified":
        _finding(findings, code="design_category_missing", related_field="Design category", field_path="design_category", reason="Classify the design as combinational, sequential, or mixed.", severity="blocking", expected_answer_type="choice")

    parameter_names: set[str] = set()
    parameters_by_name: dict[str, Parameter] = {}
    for index, parameter in enumerate(specification.parameters):
        path = f"parameters[{index}]"
        if not parameter.name:
            _finding(findings, code="parameter_name_missing", related_field="Parameter name", field_path=f"{path}.name", reason="Each parameter needs a unique identifier.", severity="blocking", expected_answer_type="text")
        elif parameter.name in parameter_names:
            _finding(findings, code="duplicate_parameter", related_field=parameter.name, field_path=f"{path}.name", reason="Parameter names must be unique within the project specification.", severity="blocking", expected_answer_type="text")
        parameter_names.add(parameter.name)
        parameters_by_name[parameter.name] = parameter
    for index, parameter in enumerate(specification.parameters):
        path = f"parameters[{index}]"
        if isinstance(parameter.width, str) and parameter.width not in parameter_names:
            _finding(findings, code="unknown_parameter_reference", related_field=parameter.name or "Parameter", field_path=f"{path}.width", reason=f"Width references unknown parameter {parameter.width!r}.", severity="blocking", expected_answer_type="positive_integer_or_parameter")

    port_names: set[str] = set()
    port_by_name: dict[str, Port] = {}
    for index, port in enumerate(specification.ports):
        path = f"ports[{index}]"
        if not port.name:
            _finding(findings, code="port_name_missing", related_field="Port name", field_path=f"{path}.name", reason="Each port needs a stable identifier.", severity="blocking", expected_answer_type="text")
        elif port.name in port_names:
            _finding(findings, code="duplicate_port", related_field=port.name, field_path=f"{path}.name", reason="Port names must be unique within the top-level interface.", severity="blocking", expected_answer_type="text")
        port_names.add(port.name)
        port_by_name[port.name] = port
        if port.direction == "unspecified":
            _finding(findings, code="port_direction_missing", related_field=port.name or "Port", field_path=f"{path}.direction", reason="Specify whether this port is an input, output, or bidirectional signal.", severity="blocking", expected_answer_type="choice")
        if port.width is None:
            _finding(findings, code="port_width_missing", related_field=port.name or "Port", field_path=f"{path}.width", reason="Specify a positive width or a simple parameter reference.", severity="blocking", expected_answer_type="positive_integer_or_parameter")
        elif isinstance(port.width, str) and port.width not in parameter_names:
            _finding(findings, code="unknown_width_parameter", related_field=port.name or "Port", field_path=f"{path}.width", reason=f"Width references unknown parameter {port.width!r}.", severity="blocking", expected_answer_type="positive_integer_or_parameter")
        if port.signedness == "unspecified":
            _finding(findings, code="port_signedness_unspecified", related_field=port.name or "Port", field_path=f"{path}.signedness", reason="State whether arithmetic on this port is signed or unsigned.", severity="advisory", expected_answer_type="choice")

    clock_names: set[str] = set()
    clock_signals: set[str] = set()
    for index, clock in enumerate(specification.clock_domains):
        path = f"clock_domains[{index}]"
        if not clock.name:
            _finding(findings, code="clock_name_missing", related_field="Clock domain", field_path=f"{path}.name", reason="Name each clock domain.", severity="blocking", expected_answer_type="text")
        elif clock.name in clock_names:
            _finding(findings, code="duplicate_clock_domain", related_field=clock.name, field_path=f"{path}.name", reason="Clock domain names must be unique.", severity="blocking", expected_answer_type="text")
        clock_names.add(clock.name)
        if not clock.signal:
            _finding(findings, code="clock_signal_missing", related_field=clock.name or "Clock domain", field_path=f"{path}.signal", reason="Associate the clock domain with a port signal.", severity="blocking", expected_answer_type="text")
        elif clock.signal not in port_names:
            _finding(findings, code="clock_signal_unknown", related_field=clock.name or "Clock domain", field_path=f"{path}.signal", reason=f"Clock signal {clock.signal!r} is not a declared port.", severity="blocking", expected_answer_type="text")
        elif clock.signal in clock_signals:
            _finding(findings, code="duplicate_clock_signal", related_field=clock.signal, field_path=f"{path}.signal", reason="A port signal cannot be assigned to multiple clock domains in this draft.", severity="blocking", expected_answer_type="text")
        clock_signals.add(clock.signal)
        if clock.edge == "unspecified":
            _finding(findings, code="clock_edge_unspecified", related_field=clock.name or "Clock domain", field_path=f"{path}.edge", reason="Specify the active clock edge.", severity="advisory", expected_answer_type="choice")
        if clock.frequency_hz is None:
            _finding(findings, code="clock_frequency_unknown", related_field=clock.name or "Clock domain", field_path=f"{path}.frequency_hz", reason="Frequency is unknown; provide it if timing analysis will depend on it.", severity="advisory", expected_answer_type="positive_integer_or_parameter")

    if specification.design_category in {"sequential", "mixed"} and not specification.clock_domains:
        _finding(findings, code="clock_required", related_field="Clock domains", field_path="clock_domains", reason="Sequential and mixed designs need at least one clock domain.", severity="blocking", expected_answer_type="object")
    if specification.design_category in {"sequential", "mixed"} and specification.reset_intent == "unspecified":
        _finding(findings, code="reset_intent_missing", related_field="Reset intent", field_path="reset_intent", reason="State whether reset is required or explicitly not intended.", severity="blocking", expected_answer_type="choice")
    if specification.reset_intent == "required" and not specification.reset_signals:
        _finding(findings, code="reset_signal_missing", related_field="Reset signals", field_path="reset_signals", reason="Reset is required but no reset signal is defined.", severity="blocking", expected_answer_type="object")
    reset_names: set[str] = set()
    for index, reset in enumerate(specification.reset_signals):
        path = f"reset_signals[{index}]"
        if not reset.name:
            _finding(findings, code="reset_name_missing", related_field="Reset signal", field_path=f"{path}.name", reason="Name each reset signal.", severity="blocking", expected_answer_type="text")
        elif reset.name in reset_names:
            _finding(findings, code="duplicate_reset", related_field=reset.name, field_path=f"{path}.name", reason="Reset signal names must be unique.", severity="blocking", expected_answer_type="text")
        reset_names.add(reset.name)
        if not reset.signal or reset.signal not in port_names:
            _finding(findings, code="reset_signal_unknown", related_field=reset.name or "Reset signal", field_path=f"{path}.signal", reason="Associate reset with a declared port signal.", severity="blocking", expected_answer_type="text")
        if reset.polarity == "unspecified":
            _finding(findings, code="reset_polarity_missing", related_field=reset.name or "Reset signal", field_path=f"{path}.polarity", reason="Specify the active reset polarity.", severity="blocking", expected_answer_type="choice")
        if reset.behavior == "unspecified":
            _finding(findings, code="reset_behavior_missing", related_field=reset.name or "Reset signal", field_path=f"{path}.behavior", reason="Specify whether reset is synchronous or asynchronous.", severity="blocking", expected_answer_type="choice")
        if reset.behavior == "synchronous" and (not reset.associated_clock_domain or reset.associated_clock_domain not in clock_names):
            _finding(findings, code="reset_clock_unknown", related_field=reset.name or "Reset signal", field_path=f"{path}.associated_clock_domain", reason="Synchronous reset must reference a declared clock domain.", severity="blocking", expected_answer_type="text")
        if reset.intended_reset_state is None:
            _finding(findings, code="reset_state_unknown", related_field=reset.name or "Reset signal", field_path=f"{path}.intended_reset_state", reason="State the intended reset state of the circuit.", severity="advisory", expected_answer_type="choice")

    requirement_ids: set[str] = set()
    for index, requirement in enumerate(specification.functional_requirements):
        path = f"functional_requirements[{index}]"
        if not requirement.id:
            _finding(findings, code="requirement_id_missing", related_field="Functional requirement", field_path=f"{path}.id", reason="Each requirement needs a stable requirement ID.", severity="blocking", expected_answer_type="text")
        elif requirement.id in requirement_ids:
            _finding(findings, code="duplicate_requirement_id", related_field=requirement.id, field_path=f"{path}.id", reason="Requirement IDs must be unique.", severity="blocking", expected_answer_type="text")
        requirement_ids.add(requirement.id)
        if not requirement.title.strip() or not requirement.statement.strip():
            _finding(findings, code="requirement_text_missing", related_field=requirement.id or "Requirement", field_path=f"{path}.statement", reason="Give each requirement a title and behavioral statement.", severity="blocking", expected_answer_type="text")

    module_names = {module.name for module in specification.modules if module.name}
    if not specification.modules:
        _finding(findings, code="module_plan_missing", related_field="Module plan", field_path="modules", reason="Add at least one planned module and identify the top level.", severity="blocking", expected_answer_type="object")
    top_levels = [module for module in specification.modules if module.is_top_level]
    if specification.modules and len(top_levels) != 1:
        _finding(findings, code="top_level_module_invalid", related_field="Top-level module", field_path="modules", reason="Identify exactly one top-level module.", severity="blocking", expected_answer_type="choice")
    seen_modules: set[str] = set()
    for index, module in enumerate(specification.modules):
        path = f"modules[{index}]"
        if not module.name:
            _finding(findings, code="module_name_missing", related_field="Module", field_path=f"{path}.name", reason="Each planned module needs a unique name.", severity="blocking", expected_answer_type="text")
        elif module.name in seen_modules:
            _finding(findings, code="duplicate_module_name", related_field=module.name, field_path=f"{path}.name", reason="Module names must be unique within the design plan.", severity="blocking", expected_answer_type="text")
        seen_modules.add(module.name)
        for field_name, references in (("input_ports", module.input_ports), ("output_ports", module.output_ports)):
            for port_name in references:
                if port_name not in port_names:
                    _finding(findings, code="module_port_unknown", related_field=module.name or "Module", field_path=f"{path}.{field_name}", reason=f"Module references nonexistent port {port_name!r}.", severity="blocking", expected_answer_type="text")
                elif field_name == "input_ports" and port_by_name[port_name].direction not in {"input", "inout", "unspecified"}:
                    _finding(findings, code="module_input_direction_invalid", related_field=module.name or "Module", field_path=f"{path}.{field_name}", reason=f"Port {port_name!r} is not an input or bidirectional port.", severity="blocking", expected_answer_type="choice")
                elif field_name == "output_ports" and port_by_name[port_name].direction not in {"output", "inout", "unspecified"}:
                    _finding(findings, code="module_output_direction_invalid", related_field=module.name or "Module", field_path=f"{path}.{field_name}", reason=f"Port {port_name!r} is not an output or bidirectional port.", severity="blocking", expected_answer_type="choice")
        for requirement_id in module.requirement_ids:
            if requirement_id not in requirement_ids:
                _finding(findings, code="module_requirement_unknown", related_field=module.name or "Module", field_path=f"{path}.requirement_ids", reason=f"Module references nonexistent requirement {requirement_id!r}.", severity="blocking", expected_answer_type="text")
        for dependency in module.dependencies:
            if dependency not in module_names:
                _finding(findings, code="module_dependency_unknown", related_field=module.name or "Module", field_path=f"{path}.dependencies", reason=f"Module depends on nonexistent module {dependency!r}.", severity="blocking", expected_answer_type="text")
        for connection_index, connection in enumerate(module.connections):
            connection_path = f"{path}.connections[{connection_index}]"
            if connection.from_module not in module_names or connection.to_module not in module_names:
                _finding(findings, code="connection_module_unknown", related_field=module.name or "Module connection", field_path=connection_path, reason="Connection references a nonexistent module.", severity="blocking", expected_answer_type="object")
                continue
            source_module = next(item for item in specification.modules if item.name == connection.from_module)
            target_module = next(item for item in specification.modules if item.name == connection.to_module)
            if connection.from_port not in source_module.output_ports and connection.from_port not in source_module.input_ports:
                _finding(findings, code="connection_source_port_unknown", related_field=module.name or "Module connection", field_path=f"{connection_path}.from_port", reason="Connection source port is not listed on the source module.", severity="blocking", expected_answer_type="text")
            if connection.to_port not in target_module.input_ports and connection.to_port not in target_module.output_ports:
                _finding(findings, code="connection_target_port_unknown", related_field=module.name or "Module connection", field_path=f"{connection_path}.to_port", reason="Connection target port is not listed on the target module.", severity="blocking", expected_answer_type="text")
            source_port = port_by_name.get(connection.from_port)
            target_port = port_by_name.get(connection.to_port)
            source_width = _known_width(source_port.width, parameters_by_name) if source_port else None
            target_width = _known_width(target_port.width, parameters_by_name) if target_port else None
            if source_port and target_port and source_width is not None and target_width is not None and source_width != target_width:
                _finding(findings, code="connection_width_mismatch", related_field=module.name or "Module connection", field_path=connection_path, reason=f"Known widths differ ({source_width} to {target_width}).", severity="blocking", expected_answer_type="object")
            elif source_port and target_port and (isinstance(source_port.width, str) or isinstance(target_port.width, str)) and (source_width is None or target_width is None):
                _finding(findings, code="connection_symbolic_width_unresolved", related_field=module.name or "Module connection", field_path=connection_path, reason="At least one connection width is symbolic, so compatibility cannot be proven yet.", severity="advisory", expected_answer_type="object")

    objective_ids: set[str] = set()
    covered: set[str] = set()
    for index, objective in enumerate(specification.verification_objectives):
        path = f"verification_objectives[{index}]"
        if not objective.id:
            _finding(findings, code="verification_id_missing", related_field="Verification objective", field_path=f"{path}.id", reason="Each planned check needs a stable ID.", severity="blocking", expected_answer_type="text")
        elif objective.id in objective_ids:
            _finding(findings, code="duplicate_verification_id", related_field=objective.id, field_path=f"{path}.id", reason="Verification objective IDs must be unique.", severity="blocking", expected_answer_type="text")
        objective_ids.add(objective.id)
        if not objective.title.strip() or not objective.expected_behavior.strip():
            _finding(findings, code="verification_text_missing", related_field=objective.id or "Verification objective", field_path=f"{path}.expected_behavior", reason="Describe the planned check and expected behavior.", severity="blocking", expected_answer_type="text")
        for requirement_id in objective.requirement_ids:
            if requirement_id not in requirement_ids:
                _finding(findings, code="verification_requirement_unknown", related_field=objective.id or "Verification objective", field_path=f"{path}.requirement_ids", reason=f"Verification references nonexistent requirement {requirement_id!r}.", severity="blocking", expected_answer_type="text")
            covered.add(requirement_id)
    for index, requirement in enumerate(specification.functional_requirements):
        if requirement.id and requirement.id not in covered:
            _finding(findings, code="requirement_uncovered", related_field=requirement.id, field_path=f"functional_requirements[{index}]", reason="Every functional requirement should have at least one planned verification objective.", severity="blocking", expected_answer_type="object")

    for index, question in enumerate(specification.open_questions):
        if question.status == "open" and question.question.strip():
            _finding(findings, code="open_question", related_field=question.related_field or "Open question", field_path=f"open_questions[{index}]", reason=question.question, severity=question.severity, expected_answer_type="text")
    for index, assumption in enumerate(specification.assumptions):
        if assumption.text.strip() and not assumption.accepted:
            _finding(findings, code="assumption_not_accepted", related_field="Assumption", field_path=f"assumptions[{index}].accepted", reason="Review and explicitly accept or remove this assumption before implementation.", severity="advisory", expected_answer_type="boolean")

    active = _merge_previous(previous, findings)
    blocking_count = sum(1 for item in active if item.status == "open" and item.severity == "blocking")
    advisory_count = sum(1 for item in active if item.status == "open" and item.severity == "advisory")
    return ValidationReport(
        valid=blocking_count == 0,
        blocking_count=blocking_count,
        advisory_count=advisory_count,
        findings=active,
    )


class PlannerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_request: str
    specification: CircuitSpecification
    clarification_answers: list[dict[str, Any]] = Field(default_factory=list)
    project_context: dict[str, Any] = Field(default_factory=dict)
    user_instruction: str = ""


class PlannerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_specification: CircuitSpecification
    assumptions: list[Assumption] = Field(default_factory=list)
    clarification_questions: list[OpenQuestion] = Field(default_factory=list)
    proposed_modules: list[ModulePlan] = Field(default_factory=list)
    proposed_verification_objectives: list[VerificationObjective] = Field(default_factory=list)
    summary: str = Field(default="", max_length=4000)
