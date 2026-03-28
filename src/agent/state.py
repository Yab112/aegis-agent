from typing import TypedDict, Annotated, Optional
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # Conversation
    messages: Annotated[list, add_messages]
    session_id: str
    user_query: str

    # Routing
    intent: Optional[str]           # "general_qa" | "book_meeting" | "handoff"
    # book_meeting only: "availability" (list slots, ask for pick + email) vs "schedule" (create event)
    book_stage: Optional[str]
    metadata_filter: Optional[dict] # e.g. {"project_name": "car_rental_app"}

    # RAG
    retrieved_chunks: Optional[list[dict]]
    confidence_score: Optional[float]

    # Actions taken
    tool_calls: list[str]
    tool_outputs: list[dict]

    # Loop control
    iterations: int                 # Guard against infinite loops (max 5)
    should_handoff: bool

    # Final
    response: Optional[str]
    sources: Optional[list[str]]
