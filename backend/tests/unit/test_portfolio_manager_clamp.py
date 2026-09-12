"""LLM portfolio decisions must not ship hallucinated actions or sizes."""
from backend.agents.portfolio_manager import (
    PortfolioDecision,
    clamp_decisions_to_allowed,
    compute_allowed_actions,
)


def test_clamp_rejects_disallowed_action_and_caps_qty():
    allowed = {
        "AAPL": {"hold": 0, "buy": 5},
        "MSFT": {"hold": 0, "sell": 2},
    }
    decisions = {
        "AAPL": PortfolioDecision(
            action="buy", quantity=99, confidence=80, reasoning="go big",
        ),
        "MSFT": PortfolioDecision(
            action="buy", quantity=1, confidence=70, reasoning="hallucinated buy",
        ),
        "TSLA": PortfolioDecision(
            action="short", quantity=10, confidence=60, reasoning="extra ticker",
        ),
    }
    out = clamp_decisions_to_allowed(decisions, allowed, ["AAPL", "MSFT"])
    assert set(out) == {"AAPL", "MSFT"}
    assert out["AAPL"].action == "buy"
    assert out["AAPL"].quantity == 5
    assert out["MSFT"].action == "hold"
    assert out["MSFT"].quantity == 0


def test_compute_allowed_then_clamp_roundtrip():
    allowed = compute_allowed_actions(
        ["NVDA"],
        {"NVDA": 100.0},
        {"NVDA": 3},
        {"cash": 500.0, "positions": {}, "margin_requirement": 0.5, "equity": 500.0},
    )
    assert allowed["NVDA"].get("buy", 0) == 3
    out = clamp_decisions_to_allowed(
        {
            "NVDA": PortfolioDecision(
                action="buy", quantity=10, confidence=50, reasoning="too many",
            )
        },
        allowed,
        ["NVDA"],
    )
    assert out["NVDA"].action == "buy"
    assert out["NVDA"].quantity == 3
