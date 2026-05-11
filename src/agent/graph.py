from langgraph.graph import StateGraph, END

from src.agent.state import AgentState
from src.agent.router import router_node
from src.agent.nodes import (
    rag_node,
    calendar_node,
    handoff_node,
    observe_node,
    respond_node,
)


def route_after_router(state: AgentState) -> str:
    """Edge: decide which tool to call based on classified intent."""
    if state["should_handoff"] or state["intent"] == "handoff":
        return "handoff"
    if state["intent"] == "book_meeting":
        return "calendar"
    return "rag"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("router", router_node)
    graph.add_node("rag", rag_node)
    graph.add_node("calendar", calendar_node)
    graph.add_node("handoff", handoff_node)
    graph.add_node("observe", observe_node)
    graph.add_node("respond", respond_node)

    # Entry
    graph.set_entry_point("router")

    # Router → tool selection
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {
            "rag": "rag",
            "calendar": "calendar",
            "handoff": "handoff",
        },
    )

    # All tools → observe
    graph.add_edge("rag", "observe")
    graph.add_edge("calendar", "observe")
    graph.add_edge("handoff", "observe")

    # Always respond after a tool — never chain back to handoff from RAG/calendar.
    # Telegram handoff only happens when router classifies intent "handoff" (rates, payment, etc.).
    graph.add_edge("observe", "respond")

    # Respond → end
    graph.add_edge("respond", END)

    return graph.compile()


# Compiled graph — import this in the API
agent = build_graph()
