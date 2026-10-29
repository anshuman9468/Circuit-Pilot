from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api import get_project, get_session, iso
from .errors import ApiError, not_found
from .models import AgentRun, Conversation, ConversationMessage, Project
from .schemas import (
    AgentRunResponse,
    ConnectionTestResponse,
    ConversationMessageResponse,
    ConversationResponse,
    MessageCreate,
    ModelConfigResponse,
    RunEventResponse,
    UsageResponse,
)
from .services.agent_runs import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    RunManager,
    append_event,
    append_message,
    append_project_event,
)
from .services.model_provider import ProviderError


router = APIRouter(prefix="/api")


def _conversation(session: Session, project_id: str, *, create: bool = True) -> Conversation | None:
    conversation = session.scalar(select(Conversation).where(Conversation.project_id == project_id))
    if conversation is None and create:
        conversation = Conversation(project_id=project_id)
        session.add(conversation)
        session.flush()
        session.commit()
        session.refresh(conversation)
    return conversation


def _run_response(run: AgentRun) -> AgentRunResponse:
    return AgentRunResponse(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        model=run.model,
        rounds=run.rounds,
        tool_calls=run.tool_calls,
        usage=UsageResponse(
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            total_tokens=run.total_tokens,
        ),
        error_code=run.error_code,
        error_message=run.error_message,
        cancel_requested=run.cancel_requested,
        created_at=iso(run.created_at),
        started_at=iso(run.started_at) if run.started_at else None,
        completed_at=iso(run.completed_at) if run.completed_at else None,
        events=[
            RunEventResponse(
                id=event.id,
                event_type=event.event_type,
                summary=event.summary,
                metadata=event.metadata_json,
                created_at=iso(event.created_at),
            )
            for event in sorted(run.events, key=lambda item: (item.created_at, item.id))
        ],
    )


def _get_run(session: Session, project_id: str, run_id: str) -> AgentRun:
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.project_id == project_id))
    if run is None:
        raise not_found("Run")
    return run


def _config_status(request: Request) -> ModelConfigResponse:
    settings = request.app.state.settings
    provider = request.app.state.model_provider
    provider_label = getattr(provider, "provider_label", "Model")
    model = getattr(provider, "model", settings.meta_model)
    base_url = getattr(provider, "base_url", settings.meta_base_url)
    fallback_configured = bool(getattr(provider, "fallback_configured", False))
    configured = bool(provider.configured)
    if not configured:
        current_status = "not_configured"
        message = "Configure a backend model key to enable conversations."
    elif request.app.state.model_connection_status == "connected":
        current_status = "connected"
        message = f"{provider_label} returned a successful response for this backend process."
    elif request.app.state.model_last_error:
        current_status = "connection_failed"
        message = "The last model request failed. Retry the connection test or a conversation explicitly."
    else:
        current_status = "configured_not_tested"
        message = "A backend key is present, but no billable connection test has been run yet."
    return ModelConfigResponse(
        configured=configured,
        status=current_status,
        model=model,
        base_url=base_url,
        message=message,
        last_error=request.app.state.model_last_error,
        provider=provider_label,
        fallback_configured=fallback_configured,
    )


@router.get("/config/model", response_model=ModelConfigResponse)
def model_config_status(request: Request) -> ModelConfigResponse:
    return _config_status(request)


@router.post("/config/model/test", response_model=ConnectionTestResponse)
async def test_model_connection(request: Request) -> ConnectionTestResponse:
    provider = request.app.state.model_provider
    if not provider.configured:
        raise ApiError(503, "not_configured", "Configure a backend model key before testing the model connection.")
    try:
        response = await provider.test_connection()
    except ProviderError as exc:
        request.app.state.model_connection_status = "connection_failed"
        request.app.state.model_last_error = exc.message
        raise ApiError(_provider_status_code(exc.code), exc.code, exc.message) from exc
    request.app.state.model_connection_status = "connected"
    request.app.state.model_last_error = None
    return ConnectionTestResponse(
        status="connected",
        model=getattr(provider, "model", request.app.state.settings.meta_model),
        message=response.content or "The configured model returned a successful response.",
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
    )


@router.get("/projects/{project_id}/conversation", response_model=ConversationResponse)
def get_conversation(project_id: str, session: Session = Depends(get_session)) -> ConversationResponse:
    get_project(session, project_id)
    conversation = _conversation(session, project_id)
    messages = session.scalars(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.visibility.in_(["user", "visible"]),
            ConversationMessage.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessage.ordinal.asc())
    ).all()
    latest_run = session.scalar(
        select(AgentRun)
        .where(AgentRun.project_id == project_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    )
    return ConversationResponse(
        conversation_id=conversation.id,
        messages=[
            ConversationMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=iso(message.created_at),
                run_id=message.run_id,
            )
            for message in messages
        ],
        latest_run=_run_response(latest_run) if latest_run else None,
    )


async def _check_capacity(request: Request, session: Session, project_id: str) -> None:
    active_for_project = session.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.project_id == project_id,
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    ) or 0
    if active_for_project:
        raise ApiError(409, "run_in_progress", "This project already has an active model request.")
    active_total = session.scalar(
        select(func.count(AgentRun.id)).where(AgentRun.status.in_(ACTIVE_RUN_STATUSES))
    ) or 0
    if active_total >= request.app.state.settings.max_active_runs:
        raise ApiError(409, "active_run_limit", "The configured active-run limit has been reached. Wait for the current run to finish.")


@router.post("/projects/{project_id}/conversation/messages", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_message(
    project_id: str,
    payload: MessageCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentRunResponse:
    get_project(session, project_id)
    if not request.app.state.model_provider.configured:
        raise ApiError(503, "not_configured", "Configure the backend model key before starting a conversation.")
    if len(payload.content.encode("utf-8")) > request.app.state.settings.max_user_message_chars:
        raise ApiError(413, "message_too_large", "The message is larger than the configured limit.")
    async with request.app.state.run_manager.lock:
        await _check_capacity(request, session, project_id)
        conversation = _conversation(session, project_id)
        run = AgentRun(
            project_id=project_id,
            conversation_id=conversation.id,
            user_message_id="pending",
            status="queued",
            model=getattr(request.app.state.model_provider, "model", request.app.state.settings.meta_model),
        )
        session.add(run)
        session.flush()
        user_message = append_message(
            session,
            conversation_id=conversation.id,
            project_id=project_id,
            run_id=run.id,
            role="user",
            visibility="user",
            content=payload.content,
        )
        session.flush()
        run.user_message_id = user_message.id
        append_event(session, run, "queued", "Request queued")
        append_project_event(session, project_id, "Conversation message sent", {"run_id": run.id})
        session.commit()
        session.refresh(run)
        await request.app.state.run_manager.start(run.id)
    return _run_response(run)


@router.get("/projects/{project_id}/runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(project_id: str, run_id: str, session: Session = Depends(get_session)) -> AgentRunResponse:
    get_project(session, project_id)
    return _run_response(_get_run(session, project_id, run_id))


@router.post("/projects/{project_id}/runs/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_agent_run(
    project_id: str,
    run_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentRunResponse:
    get_project(session, project_id)
    async with request.app.state.run_manager.lock:
        run = _get_run(session, project_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return _run_response(run)
        run.cancel_requested = True
        append_event(session, run, "cancel_requested", "Cancellation requested")
        session.commit()
        cancelled_task = await request.app.state.run_manager.cancel(run.id)
        if not cancelled_task and run.status in ACTIVE_RUN_STATUSES:
            run.status = "cancelled"
            run.error_code = "cancelled"
            run.error_message = "Run cancelled before model processing began."
            append_event(session, run, "cancelled", "Run cancelled")
            from datetime import datetime, timezone

            run.completed_at = datetime.now(timezone.utc)
            session.commit()
        session.refresh(run)
        return _run_response(run)


@router.post("/projects/{project_id}/runs/{run_id}/retry", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_agent_run(
    project_id: str,
    run_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentRunResponse:
    get_project(session, project_id)
    if not request.app.state.model_provider.configured:
        raise ApiError(503, "not_configured", "Configure the backend model key before retrying.")
    async with request.app.state.run_manager.lock:
        previous = _get_run(session, project_id, run_id)
        if previous.status not in {"failed", "cancelled", "interrupted", "incomplete"}:
            raise ApiError(409, "run_not_retryable", "Only failed, cancelled, interrupted, or incomplete runs can be retried.")
        await _check_capacity(request, session, project_id)
        run = AgentRun(
            project_id=project_id,
            conversation_id=previous.conversation_id,
            user_message_id=previous.user_message_id,
            retry_of_run_id=previous.id,
            status="queued",
            model=getattr(request.app.state.model_provider, "model", request.app.state.settings.meta_model),
        )
        session.add(run)
        session.flush()
        append_event(session, run, "queued", "Retry queued", {"retry_of_run_id": previous.id})
        session.commit()
        session.refresh(run)
        await request.app.state.run_manager.start(run.id)
    return _run_response(run)


def _provider_status_code(code: str) -> int:
    return {
        "authentication_failed": 401,
        "access_restricted": 403,
        "rate_limited": 429,
        "provider_timeout": 504,
        "connection_failed": 502,
        "invalid_provider_request": 502,
        "malformed_provider_response": 502,
    }.get(code, 502)
