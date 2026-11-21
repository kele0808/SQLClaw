"""Temporary mount check: POST /api/v1/agent/echo. Deleted by Spec 08."""

from fastapi import APIRouter
from pydantic import BaseModel

from text2sql.agent.graph import run_echo
from text2sql.common.tracing import get_trace_id

router = APIRouter(prefix="/agent", tags=["agent"])


class EchoRequest(BaseModel):
    question: str


class EchoResponse(BaseModel):
    answer: str
    trace_id: str


@router.post("/echo")
def echo(request: EchoRequest) -> EchoResponse:
    answer = run_echo(request.question)
    return EchoResponse(answer=answer, trace_id=get_trace_id() or "")
