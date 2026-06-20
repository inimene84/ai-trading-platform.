# QuantumTrade Pro — MCP Server
# Thin HTTP proxy exposing the trading backend to MCP agents. No DB/exchange/model
# credentials needed; only network access to the backend.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=9100 \
    BACKEND_BASE_URL=http://ai-trading-backend:8000 \
    BACKEND_API_PREFIX=/api

WORKDIR /app

# Install deps first for better layer caching
COPY mcp_server/requirements.txt /app/mcp_server/requirements.txt
RUN pip install --no-cache-dir -r /app/mcp_server/requirements.txt

# Copy the server package (single module, no backend code required)
COPY mcp_server/__init__.py /app/mcp_server/__init__.py
COPY mcp_server/server.py /app/mcp_server/server.py

EXPOSE 9100

CMD ["python", "-m", "mcp_server.server"]
