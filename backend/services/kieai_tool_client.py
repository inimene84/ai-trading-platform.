"""
kie.ai Claude Proxy - Tool-Calling Client

kie.ai's /claude/v1/messages endpoint does NOT support native tool_use blocks.
This module provides a wrapper that implements tool calling via:
  - System prompt injection (tool schema description)
  - Structured JSON output parsing
  - Manual agentic loop for multi-turn tool calls

Usage:
    from backend.services.kieai_tool_client import KieAIClient

    client = KieAIClient()
    result = client.run(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "What's the weather in Boston?"}],
        tools=[get_current_weather_tool],
        tool_executor=my_tool_executor,
        thinking=True,   # thinkingFlag - only works without native tools
        max_turns=5,
    )
"""

import http.client
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=True)
logger = logging.getLogger(__name__)


class KieAIClient:
    """
    Claude client for kie.ai proxy with prompt-based tool calling.

    kie.ai proxy limitations:
    - /claude/v1/messages: works for text + thinking, but tools param returns empty response
    - No OpenAI-compatible endpoint available
    - thinkingFlag works ONLY without tools in request body

    Strategy:
    - Never send 'tools' key in API payload
    - Describe tools in system prompt using XML schema
    - Ask model to respond with JSON tool_call when a tool is needed
    - Execute tool locally, feed result back, continue conversation
    """

    TOOL_CALL_SYSTEM = """\
You have access to the following tools:

{tool_schemas}

To call a tool, respond ONLY with a raw JSON object (no markdown, no code fences) in this exact format:
{{"tool_call": {{"name": "<tool_name>", "input": {{"param": "value"}}}}}}

Rules:
- Use a tool call ONLY when you need real data to answer the question.
- After receiving a tool result, provide your final answer as normal text.
- Never invent tool results — always call the tool and wait for the response.
- You may call multiple tools in sequence if needed.
"""

    def __init__(self):
        self.api_key = os.getenv('KIE_API_KEY', '')
        self.host = 'api.kie.ai'
        self.path = '/claude/v1/messages'

    def _call_api(
        self,
        model: str,
        messages: list[dict],
        system: Optional[str] = None,
        thinking: bool = False,
        max_tokens: int = 4096,
    ) -> dict:
        """Make a raw API call to kie.ai - no tools key, optional system + thinkingFlag."""
        payload: dict[str, Any] = {
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens,
            'stream': False,
        }
        if system:
            payload['system'] = system
        if thinking:
            payload['thinkingFlag'] = True

        conn = http.client.HTTPSConnection(self.host)
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        try:
            conn.request('POST', self.path, json.dumps(payload), headers)
            res = conn.getresponse()
            data = res.read().decode('utf-8')
            if res.status != 200:
                logger.error(f'kie.ai API error {res.status}: {data[:300]}')
                return {'error': f'HTTP {res.status}', 'raw': data}
            return json.loads(data)
        except Exception as e:
            logger.error(f'kie.ai API call failed: {e}')
            return {'error': str(e)}
        finally:
            conn.close()

    def _extract_text(self, response: dict) -> str:
        """Extract all text content from API response."""
        text = ''
        for block in response.get('content', []):
            if block.get('type') == 'text':
                text += block.get('text', '')
            elif block.get('type') == 'thinking':
                # Optionally include thinking for debugging
                pass
        return text

    def _parse_tool_call(self, text: str) -> Optional[dict]:
        """
        Extract JSON tool_call from model response.
        Handles various formatting (code fences, inline JSON).
        """
        # Try code-fenced JSON first
        patterns = [
            r'```json\s*({.*?"tool_call".*?})\s*```',
            r'```\s*({.*?"tool_call".*?})\s*```',
            r'({\s*"tool_call"\s*:{.*?}})',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(1))
                    if 'tool_call' in parsed:
                        return parsed['tool_call']
                except json.JSONDecodeError:
                    continue

        # Fallback: find first { containing tool_call
        idx = text.find('"tool_call"')
        if idx == -1:
            return None
        # Walk back to find opening brace
        start = text.rfind('{', 0, idx)
        if start == -1:
            return None
        # Walk forward to find matching close
        depth, end = 0, start
        for i, c in enumerate(text[start:], start):
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            parsed = json.loads(text[start:end])
            return parsed.get('tool_call')
        except json.JSONDecodeError:
            return None

    def _build_tool_system(self, tools: list[dict]) -> str:
        """Build system prompt describing available tools.
        
        Avoids embedding raw JSON schemas (curly braces break kie.ai proxy).
        Uses natural language parameter descriptions instead.
        """
        schemas = []
        for t in tools:
            props = t.get('input_schema', {}).get('properties', {})
            required = t.get('input_schema', {}).get('required', [])
            param_lines = []
            for pname, pdef in props.items():
                req_marker = ' (required)' if pname in required else ' (optional)'
                ptype = pdef.get('type', 'string')
                pdesc = pdef.get('description', '')
                param_lines.append(f'  - {pname} [{ptype}]{req_marker}: {pdesc}')
            params_str = '\n'.join(param_lines) if param_lines else '  (no parameters)'
            schemas.append(
                f'Tool: {t["name"]}\n'
                f'Description: {t.get("description", "")}\n'
                f'Parameters:\n{params_str}'
            )
        return self.TOOL_CALL_SYSTEM.format(tool_schemas='\n\n'.join(schemas))

    def run(
        self,
        model: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_executor: Optional[Callable[[str, dict], Any]] = None,
        system: Optional[str] = None,
        thinking: bool = False,
        max_tokens: int = 4096,
        max_turns: int = 6,
    ) -> dict:
        """
        Run model with optional tool support.

        Args:
            model: Model name (e.g. 'claude-haiku-4-5')
            messages: List of {role, content} dicts
            tools: List of tool schemas (Anthropic format)
            tool_executor: Callable(tool_name, tool_input) -> result_str
            system: Optional system prompt (merged with tool instructions if tools provided)
            thinking: Enable thinkingFlag (extended reasoning). Cannot combine with tool calls.
            max_tokens: Max tokens for response
            max_turns: Max agentic loop iterations

        Returns:
            dict with 'content' (final text), 'tool_calls' (list), 'thinking' (if any), 'turns'
        """
        # Build system prompt
        tool_system = self._build_tool_system(tools) if tools else ''
        combined_system = '\n\n'.join(filter(None, [tool_system, system]))

        # Note: when using tool emulation, we use thinkingFlag only on first turn
        # and only if no tools were actually called (to avoid incompatibility)
        use_thinking = thinking and bool(tools)  # thinking + tool emulation is OK since no native tools sent

        conversation = list(messages)
        tool_calls_made = []
        final_text = ''
        thinking_content = ''

        for turn in range(max_turns):
            response = self._call_api(
                model=model,
                messages=conversation,
                system=combined_system or None,
                thinking=use_thinking and turn == 0,  # thinking only on first turn
                max_tokens=max_tokens,
            )

            if 'error' in response:
                return {
                    'content': '',
                    'error': response['error'],
                    'tool_calls': tool_calls_made,
                    'turns': turn + 1,
                }

            # Capture thinking blocks
            for block in response.get('content', []):
                if block.get('type') == 'thinking':
                    thinking_content = block.get('thinking', '')

            text = self._extract_text(response)
            if not text:
                logger.warning(f'Empty response on turn {turn + 1}')
                break

            # Try to detect a tool call in the response
            tool_call = self._parse_tool_call(text) if tools else None

            if tool_call and tool_executor:
                tool_name = tool_call.get('name', '')
                tool_input = tool_call.get('input', {})
                logger.info(f'Tool call: {tool_name}({tool_input})')

                try:
                    tool_result = tool_executor(tool_name, tool_input)
                    tool_result_str = str(tool_result)
                except Exception as e:
                    tool_result_str = f'Error executing tool: {e}'
                    logger.error(f'Tool execution error: {e}')

                tool_calls_made.append({
                    'name': tool_name,
                    'input': tool_input,
                    'result': tool_result_str,
                    'turn': turn + 1,
                })

                # Add assistant response + tool result to conversation
                conversation.append({'role': 'assistant', 'content': text})
                conversation.append({
                    'role': 'user',
                    'content': f'Tool result for {tool_name}: {tool_result_str}\n\nNow provide your final answer.',
                })
                # Continue loop for final answer
            else:
                # No tool call - this is the final answer
                final_text = text
                break

        return {
            'content': final_text,
            'thinking': thinking_content,
            'tool_calls': tool_calls_made,
            'turns': len(tool_calls_made) + 1,
            'stop_reason': response.get('stop_reason', 'end_turn'),
        }


# ── Convenience factory ───────────────────────────────────────────────────────
def create_kieai_client() -> KieAIClient:
    return KieAIClient()


# ── Example usage / self-test ─────────────────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    # Define tools
    tools = [
        {
            'name': 'get_current_weather',
            'description': 'Get the current weather in a given location',
            'input_schema': {
                'type': 'object',
                'properties': {
                    'location': {'type': 'string', 'description': 'City and state, e.g. Boston, MA'}
                },
                'required': ['location'],
            }
        }
    ]

    # Define tool executor
    def my_tools(name: str, inputs: dict) -> str:
        if name == 'get_current_weather':
            return f"Sunny 68°F in {inputs.get('location')}. Light breeze."
        return 'Tool not found'

    client = KieAIClient()
    result = client.run(
        model='claude-haiku-4-5',
        messages=[{'role': 'user', 'content': 'What is the weather like in Boston today?'}],
        tools=tools,
        tool_executor=my_tools,
        thinking=True,
        max_turns=5,
    )

    print('\n=== RESULT ===')
    print(f'Turns: {result["turns"]}')
    print(f'Tool calls: {result["tool_calls"]}')
    print(f'Thinking: {result["thinking"][:100] if result["thinking"] else "none"}...')
