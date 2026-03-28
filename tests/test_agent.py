"""
Tests for the agent router and tool nodes.
Run with: pytest tests/test_agent.py -v
"""
import pytest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage


# ──────────────────────────────────────────────────────────────────────────────
# ROUTER TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestRouter:
    def _make_state(self, query: str) -> dict:
        return {
            "messages": [HumanMessage(content=query)],
            "session_id": "test-session",
            "user_query": query,
            "intent": None,
            "metadata_filter": None,
            "retrieved_chunks": None,
            "confidence_score": None,
            "tool_calls": [],
            "tool_outputs": [],
            "iterations": 0,
            "should_handoff": False,
            "response": None,
            "sources": None,
        }

    def test_rate_query_triggers_immediate_handoff(self):
        """Hard-coded triggers should bypass LLM call."""
        from src.agent.router import router_node
        state = self._make_state("What are your rates for a 6-month contract?")
        with patch("src.agent.router.model") as _:  # LLM should NOT be called
            result = router_node(state)
        assert result["intent"] == "handoff"
        assert result["should_handoff"] is True

    def test_pricing_trigger(self):
        from src.agent.router import router_node
        state = self._make_state("How much do you charge for a MVP?")
        result = router_node(state)
        assert result["should_handoff"] is True

    @patch("src.agent.router.model")
    def test_project_question_routes_to_general_qa(self, mock_model):
        mock_model.generate_content.return_value.text = (
            '{"intent": "general_qa", "project_filter": "car_rental_app", "reason": "asking about project"}'
        )
        from src.agent.router import router_node
        state = self._make_state("Tell me about the car rental app")
        result = router_node(state)
        assert result["intent"] == "general_qa"
        assert result["metadata_filter"] == {"project_name": "car_rental_app"}

    @patch("src.agent.router.model")
    def test_meeting_request_routes_to_book_meeting(self, mock_model):
        mock_model.generate_content.return_value.text = (
            '{"intent": "book_meeting", "project_filter": null, "reason": "wants a call"}'
        )
        from src.agent.router import router_node
        state = self._make_state("Can we schedule a discovery call?")
        result = router_node(state)
        assert result["intent"] == "book_meeting"

    @patch("src.agent.router.model")
    def test_bad_json_from_llm_falls_back_to_general_qa(self, mock_model):
        mock_model.generate_content.return_value.text = "not valid json at all"
        from src.agent.router import router_node
        state = self._make_state("Tell me something")
        result = router_node(state)
        assert result["intent"] == "general_qa"


# ──────────────────────────────────────────────────────────────────────────────
# HANDOFF TOOL TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestHandoffTool:
    @patch("src.tools.handoff_tool.httpx.post")
    def test_successful_send_returns_true(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()
        from src.tools.handoff_tool import send_whatsapp_briefing
        result = send_whatsapp_briefing(
            query="What is your rate?",
            intent="handoff",
            session_id="test-123",
            user_email="client@example.com",
        )
        assert result is True

    @patch("src.tools.handoff_tool.httpx.post", side_effect=Exception("Network error"))
    def test_failed_send_returns_false_gracefully(self, mock_post):
        """WhatsApp failure should NOT crash the agent."""
        from src.tools.handoff_tool import send_whatsapp_briefing
        result = send_whatsapp_briefing(
            query="What is your rate?",
            intent="handoff",
            session_id="test-123",
        )
        assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# GRAPH ROUTING TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestGraphRouting:
    def test_route_after_router_handoff(self):
        from src.agent.graph import route_after_router
        state = {"should_handoff": True, "intent": "handoff"}
        assert route_after_router(state) == "handoff"

    def test_route_after_router_calendar(self):
        from src.agent.graph import route_after_router
        state = {"should_handoff": False, "intent": "book_meeting"}
        assert route_after_router(state) == "calendar"

    def test_route_after_router_rag(self):
        from src.agent.graph import route_after_router
        state = {"should_handoff": False, "intent": "general_qa"}
        assert route_after_router(state) == "rag"

    def test_route_after_observe_max_iterations(self):
        from src.agent.graph import route_after_observe
        state = {
            "iterations": 5,
            "should_handoff": False,
            "confidence_score": 0.9,
            "tool_calls": ["rag"],
        }
        assert route_after_observe(state) == "respond"

    def test_route_after_observe_low_confidence_triggers_handoff(self):
        from src.agent.graph import route_after_observe
        state = {
            "iterations": 1,
            "should_handoff": False,
            "confidence_score": 0.50,  # Below 0.72 threshold
            "tool_calls": ["rag"],
        }
        assert route_after_observe(state) == "handoff"
