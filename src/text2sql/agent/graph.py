"""Temporary LangGraph mount check (Spec 01 F7). Spec 08 replaces this graph."""

from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


class EchoState(TypedDict):
    question: str
    answer: str


EchoGraph = CompiledStateGraph[EchoState, None, EchoState, EchoState]


def echo_node(state: EchoState) -> dict[str, str]:
    return {"answer": state["question"]}


def build_graph() -> EchoGraph:
    graph: StateGraph[EchoState, None, EchoState, EchoState] = StateGraph(EchoState)
    graph.add_node("echo", echo_node)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph() -> EchoGraph:
    return build_graph()


def run_echo(question: str) -> str:
    result = _compiled_graph().invoke({"question": question, "answer": ""})
    answer = result["answer"]
    if not isinstance(answer, str):
        raise TypeError("echo graph returned a non-string answer")
    return answer
