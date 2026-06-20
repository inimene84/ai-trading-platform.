"""
QuantumTrade Pro — MCP Server
=============================
Exposes the live trading backend to MCP-compatible agents (Claude Desktop,
Cursor, Agent Zero, etc.) as a curated set of tools. Runs as its own
lightweight process/container and talks to the backend over HTTP, so it needs
no database, exchange, or model credentials of its own.

Two ways to expose tools:

  1. CURATED TOOLS (default, recommended) — a hand-picked, well-described set of
     high-value tools (opinion, trade memory, skills, portfolio, loop control).
     Clear names + docstrings make them reliable for LLM agents.

  2. AUTO-GENERATED (FULL_API=true) — mirror *every* FastAPI route as an MCP tool
     via FastMCP.from_fastapi. Comprehensive but noisy; use for power users.

Env:
  BACKEND_BASE_URL   default http://ai-trading-backend:8000  (Docker internal)
                     external: http://72.60.18.113:8001/api  or  http://localhost:8001/api
  BACKEND_API_PREFIX default /api   (the FastAPI app mounts routes under /api)
  MCP_API_TOKEN      optional bearer token sent to the backend
  MCP_TRANSPORT      default http   (stdio | http | sse)
  MCP_HOST           default 0.0.0.0
  MCP_PORT           default 9100
  FULL_API           default false  (true -> auto-generate from OpenAPI)

Run:
  python -m mcp_server.server
  # or
  fastmcp run mcp_server/server.py:mcp --transport http --host 0.0.0.0 --port 9100
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional

import httpx

try:
    from fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "fastmcp is required: pip install fastmcp httpx  (see mcp_server/requirements.txt)"
    ) from e


# ── Config ────────────────────────────────────────────────────────────────────
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://ai-trading-backend:8000")
API_PREFIX = os.getenv("BACKEND_API_PREFIX", "/api").rstrip("/")
API_TOKEN = os.getenv("MCP_API_TOKEN", "")
TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT", "20"))


def _headers() -> Dict[str, str]:
    h = {"Accept": "application/json"}
    if API_TOKEN:
        h["Authorization"] = f"Bearer {API_TOKEN}"
    return h


def _url(path: str) -> str:
    path = path if path.startswith("/") else "/" + path
    # Allow callers to pass either "/trading/..." or "/api/trading/..."
    if API_PREFIX and not path.startswith(API_PREFIX + "/") and path != API_PREFIX:
        path = API_PREFIX + path
    return f"{BACKEND_BASE_URL.rstrip('/')}{path}"


async def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        r = await cx.get(_url(path), params=params, headers=_headers())
        r.raise_for_status()
        return _safe_json(r)


async def _post(path: str, params: Optional[Dict[str, Any]] = None,
                json_body: Optional[Dict[str, Any]] = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
        r = await cx.post(_url(path), params=params, json=json_body, headers=_headers())
        r.raise_for_status()
        return _safe_json(r)


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {"status_code": r.status_code, "text": r.text[:2000]}


# ── Build the server (curated tools) ────────────────────────────────────────────
mcp = FastMCP(
    name="QuantumTrade Pro",
    instructions=(
        "Tools for the QuantumTrade Pro AI trading backend. Use them to inspect "
        "the multi-agent trading opinion for a symbol, query the agent's learned "
        "trade memory and mined strategy skills, view the portfolio, and control "
        "the trading/sentiment loops. All actions hit the live backend."
    ),
)


# ---- Read: market opinion ------------------------------------------------------
@mcp.tool
async def get_trading_opinion(symbol: str) -> dict:
    """Get the full multi-agent trading opinion for a symbol (e.g. "BTCUSDT").

    Returns the unified direction (BUY/SELL/HOLD), confidence, reasoning, and
    each contributing agent's vote (technical, Kronos, sentiment, personas,
    semantic_trade_memory, learned_skill, ...)."""
    return await _get(f"/trading/opinion/{symbol}")


@mcp.tool
async def get_opinion_weights() -> dict:
    """Get the current agent voting weights used to aggregate the opinion."""
    return await _get("/trading/opinion/weights")


@mcp.tool
async def set_opinion_weight(agent: str, weight: float) -> dict:
    """Set the voting weight for one agent (e.g. agent="learned_skill",
    weight=0.15). Affects how much that agent influences the final decision."""
    return await _post("/trading/opinion/weights", json_body={"weights": {agent: weight}})


# ---- Read: learning loop (Track C + skills) ------------------------------------
@mcp.tool
async def trade_memory_status() -> dict:
    """Status of the semantic trade-memory vector store (points count, config)."""
    return await _get("/trading/trade-memory/status")


@mcp.tool
async def recall_similar_trades(
    context: Dict[str, Any],
    symbol: Optional[str] = None,
    same_symbol_only: bool = False,
    limit: int = 8,
) -> dict:
    """Recall the most similar past trades for a market context and summarise how
    they resolved (win-rate, avg PnL, directional bias).

    context keys (all optional): direction, regime ("TRENDING"/"RANGING"),
    trend_signal, momentum_signal, mean_reversion_signal, volatility (0..1),
    rsi (0..100), funding_rate, sentiment_score (-1..1), kronos_change_pct."""
    body = {"context": context, "symbol": symbol,
            "same_symbol_only": same_symbol_only, "limit": limit}
    return await _post("/trading/trade-memory/recall", json_body=body)


@mcp.tool
async def trade_memory_backfill(limit: int = 1000) -> dict:
    """Vectorise existing closed trades from SQL into the trade-memory store."""
    return await _post("/trading/trade-memory/backfill", params={"limit": limit})


@mcp.tool
async def list_strategy_skills(active_only: bool = True, limit: int = 50) -> dict:
    """List the agent's learned strategy skills (the leaderboard of mined setups)."""
    return await _get("/trading/skills", params={"active_only": active_only, "limit": limit})


@mcp.tool
async def skills_leaderboard(limit: int = 10) -> dict:
    """Top learned strategy skills ranked by edge score — compact leaderboard."""
    return await _get("/trading/skills/leaderboard", params={"limit": limit})


@mcp.tool
async def mine_skills(limit: Optional[int] = None) -> dict:
    """Trigger a skill-mining pass over closed-trade history (idempotent)."""
    params = {"limit": limit} if limit else None
    return await _post("/trading/skills/mine", params=params)


@mcp.tool
async def match_skill(context: Dict[str, Any]) -> dict:
    """Match an arbitrary market context to the best learned strategy skill.

    See recall_similar_trades for the accepted context keys."""
    return await _post("/trading/skills/match", json_body={"context": context})


# ---- Read: portfolio / signals -------------------------------------------------
@mcp.tool
async def get_portfolio() -> dict:
    """Get the current portfolio (positions, equity, PnL)."""
    return await _get("/trading/portfolio")


@mcp.tool
async def get_loop_status() -> dict:
    """Get the trading loop status (running, interval, symbols, cycle count)."""
    return await _get("/trading/loop/status")


@mcp.tool
async def get_trading_config() -> dict:
    """Get the active trading configuration (mode, interval, symbols, risk limits)."""
    return await _get("/trading/config")


# ---- Read: sentiment loop (Track A) --------------------------------------------
@mcp.tool
async def sentiment_loop_status() -> dict:
    """Status of the native in-app sentiment loop (Track A)."""
    return await _get("/news/sentiment-loop/status")


@mcp.tool
async def run_sentiment_loop(dry_run: bool = True) -> dict:
    """Run one pass of the native sentiment loop. dry_run=True writes nothing."""
    return await _post("/news/sentiment-loop/run", params={"dry_run": dry_run})


# ---- Generic escape hatch ------------------------------------------------------
@mcp.tool
async def call_backend(method: str, path: str,
                       params: Optional[Dict[str, Any]] = None,
                       body: Optional[Dict[str, Any]] = None) -> dict:
    """Call an arbitrary backend endpoint. method="GET"|"POST", path like
    "/trading/strategies". Use only when no curated tool fits."""
    m = method.upper()
    if m == "GET":
        return {"result": await _get(path, params=params)}
    if m == "POST":
        return {"result": await _post(path, params=params, json_body=body)}
    raise ValueError("method must be GET or POST")


# ── Optional: auto-generate the FULL API surface from OpenAPI ────────────────────
def _maybe_full_api() -> "FastMCP":
    """If FULL_API=true, build a server that mirrors every backend route as a
    tool, fetched from the live OpenAPI schema. Returns the curated server on
    any failure."""
    if os.getenv("FULL_API", "false").lower() != "true":
        return mcp
    try:
        openapi_url = f"{BACKEND_BASE_URL.rstrip('/')}/openapi.json"
        spec = httpx.get(openapi_url, timeout=TIMEOUT, headers=_headers()).json()
        client = httpx.AsyncClient(base_url=BACKEND_BASE_URL.rstrip("/"), headers=_headers())
        full = FastMCP.from_openapi(openapi_spec=spec, client=client,
                                    name="QuantumTrade Pro (full API)")
        return full
    except Exception as e:  # pragma: no cover
        print(f"[mcp] FULL_API requested but failed ({e}); using curated tools")
        return mcp


def main():
    server = _maybe_full_api()
    transport = os.getenv("MCP_TRANSPORT", "http")
    if transport == "stdio":
        server.run()  # stdio
    else:
        server.run(
            transport=transport,
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "9100")),
        )


if __name__ == "__main__":
    main()
