"""
AI Analysis Pipeline Service
Dual-LLM cost optimization: Ollama (FREE) for research, Expensive model for final decisions.
"""

import json
import logging
import os
import re
import time
from typing import Optional

import httpx
from dotenv import load_dotenv
from pathlib import Path

_env_path = Path(__file__).resolve().parents[2] / '.env'
load_dotenv(_env_path, override=True)

def _read_env_direct(key, default=''):
    """Read directly from .env file as fallback."""
    try:
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            if k.strip() == key:
                return v.strip()
    except Exception:
        pass
    return default

logger = logging.getLogger(__name__)


class AIAnalysisService:
    """Multi-step AI analysis pipeline using dual LLM strategy."""

    def __init__(self):
        self.ollama_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        self.ollama_model = _read_env_direct('OLLAMA_PRIMARY_MODEL', 'phi3.5')
        logger.info(f"AI Analysis using Ollama model: {self.ollama_model}")
        self.expensive_model = _read_env_direct('EXPENSIVE_MODEL') or _read_env_direct('XAI_MODEL', 'grok-4-1-fast-reasoning')
        self.expensive_provider = _read_env_direct('EXPENSIVE_PROVIDER', 'xai')
        self.expensive_api_key = _read_env_direct('EXPENSIVE_API_KEY') or _read_env_direct('XAI_API_KEY', '')
        self.expensive_base_url = _read_env_direct('EXPENSIVE_BASE_URL') or _read_env_direct('XAI_BASE_URL', 'https://api.x.ai/v1')
        self.enabled = _read_env_direct('AI_ANALYSIS_ENABLED', 'true').lower() == 'true'
        self.ollama_url = _read_env_direct('OLLAMA_BASE_URL', 'http://localhost:11434')

    def reload_config(self):
        load_dotenv(_env_path, override=True)
        self.__init__()

    @property
    def models_info(self) -> dict:
        return {
            'enabled': self.enabled,
            'research_model': {
                'model': 'local-compute', 'provider': 'system',
                'role': 'Technical Indicators, Bull/Bear Analysis (instant, FREE)', 'cost': 'free',
            },
            'decision_model': {
                'model': self.expensive_model, 'provider': self.expensive_provider,
                'url': self.expensive_base_url, 'role': 'AI Trading Decision (fast, ~3-5s)',
                'cost': 'paid', 'configured': bool(self.expensive_api_key),
            },
            'pipeline_steps': [
                {'step': 1, 'name': 'Technical Analysis + Bull/Bear', 'model': 'local-compute', 'provider': 'system', 'cost': 'free'},
                {'step': 2, 'name': 'AI Decision', 'model': self.expensive_model, 'provider': self.expensive_provider, 'cost': 'paid'},
            ],
        }

    async def run_ollama(self, prompt: str, system: str = '') -> str:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                messages = []
                if system:
                    messages.append({'role': 'system', 'content': system})
                messages.append({'role': 'user', 'content': prompt})
                resp = await client.post(
                    f"{self.ollama_url}/api/chat",
                    json={'model': self.ollama_model, 'messages': messages, 'stream': False},
                )
                resp.raise_for_status()
                return resp.json().get('message', {}).get('content', 'No response')
        except httpx.TimeoutException:
            logger.error(f"Ollama timeout calling {self.ollama_model}")
            return '[ERROR] Ollama request timed out after 120s'
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return f'[ERROR] Ollama call failed: {str(e)}'

    async def run_expensive_model(self, prompt: str, system: str = '') -> dict:
        if not self.expensive_api_key:
            return self._fallback_decision('No expensive model API key configured')
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                messages = []
                if system:
                    messages.append({'role': 'system', 'content': system})
                messages.append({'role': 'user', 'content': prompt})

                # Reasoning models (grok-*-reasoning, o1, o3) don't support temperature
                is_reasoning = any(x in self.expensive_model for x in
                                   ['reasoning', 'o1', 'o3', 'o4'])
                payload = {
                    'model': self.expensive_model,
                    'messages': messages,
                    'max_tokens': 1024,
                }
                if not is_reasoning:
                    payload['temperature'] = 0.3

                resp = await client.post(
                    f"{self.expensive_base_url}/chat/completions",
                    headers={
                        'Authorization': f'Bearer {self.expensive_api_key}',
                        'Content-Type': 'application/json'
                    },
                    json=payload,
                )
                if not resp.is_success:
                    body = resp.text[:300]
                    logger.error(f"xAI API {resp.status_code}: {body}")
                    resp.raise_for_status()
                content = resp.json()['choices'][0]['message']['content']
                return self._parse_decision(content)
        except httpx.TimeoutException:
            return self._fallback_decision('Cloud model timed out')
        except Exception as e:
            logger.error(f"Expensive model error: {e}")
            return self._fallback_decision(str(e))


    def _parse_decision(self, content: str) -> dict:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        m = re.search(r'```(?:json)?\s*\n?({.*?})\s*\n?```', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r'\{[^{}]*"direction"[^{}]*\}', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        direction = 'HOLD'
        if 'BUY' in content.upper():
            direction = 'BUY'
        elif 'SELL' in content.upper():
            direction = 'SELL'
        return {'direction': direction, 'confidence': 0.5, 'reasoning': content[:500],
                'entry_price': None, 'stop_loss': None, 'take_profit': None, 'raw_response': content}

    def _fallback_decision(self, error_msg: str) -> dict:
        return {'direction': 'HOLD', 'confidence': 0.0,
                'reasoning': f'AI analysis error: {error_msg}. Defaulting to HOLD.',
                'entry_price': None, 'stop_loss': None, 'take_profit': None, 'error': error_msg}

    def _build_ohlcv_summary(self, bars: list[dict]) -> str:
        if not bars:
            return 'No data available'
        latest = bars[-1]
        price = latest.get('close', 0)
        closes = [b['close'] for b in bars if 'close' in b]
        if len(closes) < 14:
            return f"Current price: ${price:,.2f}. Insufficient data."
        gains, losses = [], []
        for i in range(1, min(15, len(closes))):
            diff = closes[-i] - closes[-i - 1]
            if diff >= 0:
                gains.append(diff)
            else:
                losses.append(abs(diff))
        avg_gain = sum(gains) / 14 if gains else 0.001
        avg_loss = sum(losses) / 14 if losses else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        ema12 = sum(closes[-12:]) / 12 if len(closes) >= 12 else price
        ema26 = sum(closes[-26:]) / 26 if len(closes) >= 26 else price
        macd = ema12 - ema26
        sma20 = sum(closes[-20:]) / min(20, len(closes))
        sma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 50 else sma20
        if len(closes) >= 20:
            mean = sum(closes[-20:]) / 20
            volatility = (sum((c - mean) ** 2 for c in closes[-20:]) / 20) ** 0.5
        else:
            volatility = 0
        pct_1d = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0
        pct_7d = ((closes[-1] - closes[-7]) / closes[-7] * 100) if len(closes) >= 7 else 0
        high_20 = max(b['high'] for b in bars[-20:])
        low_20 = min(b['low'] for b in bars[-20:])
        lines = [
            f"Current price: ${price:,.2f}",
            f"1-day change: {pct_1d:+.2f}% | 7-day change: {pct_7d:+.2f}%",
            f"RSI(14): {rsi:.1f} | MACD: {macd:+.4f}",
            f"SMA(20): ${sma20:,.2f} | SMA(50): ${sma50:,.2f}",
            f"20-bar High: ${high_20:,.2f} | Low: ${low_20:,.2f}",
            f"Volatility(20): ${volatility:,.2f}",
            f"Volume (latest): {latest.get('volume', 0):,.0f}",
        ]
        return '\n'.join(lines)

    async def analyze_symbol(self, symbol: str, bars: list[dict], strategy_signal: Optional[dict] = None) -> dict:
        """Fast AI analysis - uses computed indicators + ONE cloud LLM call."""
        started = time.time()
        steps = []
        ohlcv_summary = self._build_ohlcv_summary(bars)
        signal_str = 'No strategy signal'
        sentiment_str = 'No sentiment data'
        if strategy_signal:
            signal_str = (f"Signal: {strategy_signal.get('direction', 'NEUTRAL')} "
                          f"(confidence: {strategy_signal.get('confidence', 0):.2f})")
            sent = strategy_signal.get('sentiment')
            if sent:
                sentiment_str = (
                    f"Direction: {sent.get('direction', 'N/A')} | "
                    f"Score: {sent.get('sentiment_score', 0):+.3f} | "
                    f"Impact: {sent.get('impact_score', 0):.3f} | "
                    f"Confidence: {sent.get('confidence', 0):.3f} | "
                    f"Articles (1h): {sent.get('article_count', 0)} | "
                    f"Source: {sent.get('source', 'unknown')}"
                )

        # Build Binance-native data sections
        funding_str = 'No funding rate data'
        oi_str = 'No open interest data'
        ticker_str = 'No 24h market stats'
        if strategy_signal:
            fr = strategy_signal.get('funding_rate')
            if fr:
                rate = fr.get('fundingRate', 0)
                rate_pct = rate * 100
                direction = 'positive (longs pay shorts)' if rate > 0 else 'negative (shorts pay longs)' if rate < 0 else 'neutral'
                funding_str = (
                    f"Current Rate: {rate:.6f} ({rate_pct:+.4f}%)\n"
                    f"Direction: {direction}\n"
                    f"Interpretation: {'Bearish signal - market overleveraged long' if rate > 0.0001 else 'Bullish signal - market overleveraged short' if rate < -0.0001 else 'Neutral - balanced positioning'}"
                )
                if fr.get('markPrice'):
                    funding_str += f"\nMark Price: ${fr['markPrice']:,.2f}"

            oi = strategy_signal.get('open_interest')
            if oi:
                oi_val = oi.get('openInterest', 0)
                oi_str = f"Current OI: {oi_val:,.2f} contracts"

            tk = strategy_signal.get('ticker_24h')
            if tk:
                ticker_str = (
                    f"Price Change (24h): ${tk.get('priceChange', 0):+,.2f} ({tk.get('priceChangePercent', 0):+.2f}%)\n"
                    f"24h High: ${tk.get('highPrice', 0):,.2f} | Low: ${tk.get('lowPrice', 0):,.2f}\n"
                    f"24h Volume: {tk.get('volume', 0):,.2f} | Quote Volume: ${tk.get('quoteVolume', 0):,.0f}\n"
                    f"Trades (24h): {tk.get('count', 0):,}"
                )

        # Build n8n social sentiment section
        sentiment_str = 'No sentiment data'
        sources_str = ''
        global_sentiment_str = 'No global fear/greed data'
        market_alerts_str = 'No trending/pump alerts'
        if strategy_signal:
            sent = strategy_signal.get('sentiment')
            if sent:
                sentiment_str = (
                    f"Direction: {sent.get('direction', 'N/A')} | "
                    f"Score: {sent.get('sentiment_score', 0):+.3f} | "
                    f"Impact: {sent.get('impact_score', 0):.3f} | "
                    f"Confidence: {sent.get('confidence', 0):.3f} | "
                    f"Posts (1h): {sent.get('article_count', 0)}"
                )
                sources = sent.get('sources', {})
                if sources:
                    sources_str = "Source breakdown:\n" + "\n".join(
                        f"  • {src}: {score:+.3f}" for src, score in sources.items()
                    )

            # Market alerts from n8n (trending/pumps)
            alerts = strategy_signal.get('market_alerts', [])
            if alerts:
                lines = []
                for a in alerts[:5]:
                    lines.append(
                        f"  • {a.get('alert_type', 'alert').upper()} "
                        f"score={a.get('score', 0)} "
                        f"volume_surge={a.get('volume_surge', 0)}x"
                    )
                market_alerts_str = "\n".join(lines)

            # Global fear/greed
            global_sent = strategy_signal.get('global_sentiment')
            if global_sent:
                global_sentiment_str = (
                    f"Global sentiment: {global_sent.get('sentiment_score', 0):+.3f} | "
                    f"Impact: {global_sent.get('impact_score', 0):.3f}"
                )

        # Build Kronos AI section
        kronos_str = 'No Kronos forecast'
        if strategy_signal:
            kr = strategy_signal.get('kronos')
            if kr and not kr.get('error'):
                kronos_str = (
                    f"Signal: {kr.get('signal', 'NEUTRAL')} | "
                    f"Predicted next close: {kr.get('predicted_close', 'N/A')} | "
                    f"Change: {kr.get('predicted_change_pct', 0.0):+.2f}% | "
                    f"Confidence: {kr.get('confidence', 0.0):.4f}"
                )

        # Step 1: Technical Indicators (INSTANT - computed locally)
        t0 = time.time()
        bull_factors, bear_factors = self._compute_bull_bear(bars)
        tech_output = f"INDICATORS:\n{ohlcv_summary}\n\nBULLISH FACTORS:\n{bull_factors}\n\nBEARISH FACTORS:\n{bear_factors}"
        steps.append({'step': 'Technical Analysis', 'model': 'local-compute', 'provider': 'system',
                      'cost': 'free', 'output': tech_output, 'duration_s': round(time.time() - t0, 3)})

        # Step 2: AI Decision (ONE fast cloud call)
        decision_system = 'You are a senior portfolio manager specializing in crypto futures. Analyze the data and respond ONLY with valid JSON.'
        json_format = '{"direction": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "reasoning": "brief 1-2 sentences", "entry_price": number_or_null, "stop_loss": number_or_null, "take_profit": number_or_null}'
        decision_prompt = (f"Make a trading decision for {symbol}.\n\n"
                           f"=== Market Data ===\n{ohlcv_summary}\n\n"
                           f"=== Bullish Factors ===\n{bull_factors}\n\n"
                           f"=== Bearish Factors ===\n{bear_factors}\n\n"
                           f"=== Strategy Signal ===\n{signal_str}\n\n"
                           f"=== Social Sentiment (n8n aggregated from X/Reddit/Discord/News) ===\n{sentiment_str}\n{sources_str}\n\n"
                           f"=== Global Market Sentiment ===\n{global_sentiment_str}\n\n"
                           f"=== Trending/Pump Alerts (n8n) ===\n{market_alerts_str}\n\n"
                           f"=== Binance Funding Rate ===\n{funding_str}\n\n"
                           f"=== Open Interest ===\n{oi_str}\n\n"
                           f"=== 24h Market Stats ===\n{ticker_str}\n\n"
                           f"=== Kronos AI Model Forecast ===\nKronos is a foundation model trained on 45+ global exchanges candlestick data.\n{kronos_str}\n\n"
                           f"Respond in JSON:\n{json_format}")
        t0 = time.time()
        decision = await self.run_expensive_model(decision_prompt, decision_system)
        decision_output = json.dumps(decision, indent=2) if isinstance(decision, dict) else str(decision)
        steps.append({'step': 'AI Decision', 'model': self.expensive_model, 'provider': self.expensive_provider,
                      'cost': 'paid', 'output': decision_output, 'duration_s': round(time.time() - t0, 1)})

        total_duration = round(time.time() - started, 1)
        return {
            'symbol': symbol,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'steps': steps,
            'direction': decision.get('direction', 'HOLD'),
            'confidence': decision.get('confidence', 0.0),
            'reasoning': decision.get('reasoning', ''),
            'entry_price': decision.get('entry_price'),
            'stop_loss': decision.get('stop_loss'),
            'take_profit': decision.get('take_profit'),
            'total_duration_s': total_duration,
            'models_used': {
                'research': {'model': 'local-compute', 'provider': 'system', 'cost': 'free'},
                'decision': {'model': self.expensive_model, 'provider': self.expensive_provider, 'cost': 'paid'},
            },
        }

    def _compute_bull_bear(self, bars: list[dict]) -> tuple[str, str]:
        """Compute bull/bear factors from technical indicators (instant, no LLM needed)."""
        if not bars or len(bars) < 14:
            return 'Insufficient data', 'Insufficient data'
        closes = [b['close'] for b in bars if 'close' in b]
        price = closes[-1]
        bull, bear = [], []

        # RSI
        gains, losses = [], []
        for i in range(1, min(15, len(closes))):
            diff = closes[-i] - closes[-i - 1]
            (gains if diff >= 0 else losses).append(abs(diff))
        avg_gain = sum(gains) / 14 if gains else 0.001
        avg_loss = sum(losses) / 14 if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / max(avg_loss, 0.001)))
        if rsi < 30: bull.append(f'RSI oversold ({rsi:.0f}) — bounce likely')
        elif rsi < 45: bull.append(f'RSI neutral-low ({rsi:.0f}) — room to run')
        elif rsi > 70: bear.append(f'RSI overbought ({rsi:.0f}) — pullback risk')
        elif rsi > 55: bear.append(f'RSI elevated ({rsi:.0f}) — limited upside')

        # Moving Averages
        sma20 = sum(closes[-20:]) / min(20, len(closes))
        sma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 50 else sma20
        if price > sma20: bull.append(f'Price above SMA20 (${sma20:,.2f})')
        else: bear.append(f'Price below SMA20 (${sma20:,.2f})')
        if price > sma50: bull.append(f'Price above SMA50 (${sma50:,.2f})')
        else: bear.append(f'Price below SMA50 (${sma50:,.2f})')
        if sma20 > sma50: bull.append('Golden cross: SMA20 > SMA50')
        else: bear.append('Death cross: SMA20 < SMA50')

        # MACD
        ema12 = sum(closes[-12:]) / 12 if len(closes) >= 12 else price
        ema26 = sum(closes[-26:]) / 26 if len(closes) >= 26 else price
        macd = ema12 - ema26
        if macd > 0: bull.append(f'MACD bullish ({macd:+.4f})')
        else: bear.append(f'MACD bearish ({macd:+.4f})')

        # Momentum
        if len(closes) >= 7:
            pct_7d = (closes[-1] - closes[-7]) / closes[-7] * 100
            if pct_7d > 3: bull.append(f'Strong 7d momentum (+{pct_7d:.1f}%)')
            elif pct_7d > 0: bull.append(f'Positive 7d momentum (+{pct_7d:.1f}%)')
            elif pct_7d < -3: bear.append(f'Weak 7d momentum ({pct_7d:.1f}%)')
            else: bear.append(f'Negative 7d momentum ({pct_7d:.1f}%)')

        # Support/Resistance
        high_20 = max(b['high'] for b in bars[-20:])
        low_20 = min(b['low'] for b in bars[-20:])
        range_pct = (high_20 - low_20) / low_20 * 100
        if price < low_20 * 1.02: bull.append(f'Near 20-bar support (${low_20:,.2f})')
        if price > high_20 * 0.98: bear.append(f'Near 20-bar resistance (${high_20:,.2f})')

        # Volume
        if len(bars) >= 5:
            recent_vol = sum(b.get('volume', 0) for b in bars[-5:]) / 5
            avg_vol = sum(b.get('volume', 0) for b in bars[-20:]) / min(20, len(bars))
            if avg_vol > 0 and recent_vol > avg_vol * 1.5: bull.append('Volume surge (1.5x average)')
            elif avg_vol > 0 and recent_vol < avg_vol * 0.5: bear.append('Low volume — weak conviction')

        bull_str = '\n'.join(f'• {f}' for f in bull) if bull else '• No strong bullish signals'
        bear_str = '\n'.join(f'• {f}' for f in bear) if bear else '• No strong bearish signals'
        return bull_str, bear_str


# Singleton instance
ai_analysis_service = AIAnalysisService()
