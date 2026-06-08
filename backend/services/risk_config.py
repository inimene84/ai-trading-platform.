import os
from pydantic_settings import BaseSettings
from pydantic import AliasChoices, Field as PydanticField

class RiskConfig(BaseSettings):
    """Centralized risk and trading configuration."""
    
    # Position management
    emergency_drawdown_pct: float = -15.0
    max_position_hold_hours: float = 72.0
    exit_opinion_threshold: float = 0.65
    min_position_hold_min: int = 20
    
    # Cooldowns
    sl_cooldown_minutes: int = 30
    opinion_close_cooldown_min: int = 30
    
    # Pyramid/DCA settings
    pyramid_mode: bool = False
    pyramid_max_layers: int = 5
    pyramid_atr_multiplier: float = 1.0
    pyramid_min_conf_increase: float = 0.05
    pyramid_min_improvement: float = 0.005
    pyramid_usdt_per_layer: float = 10.0
    pyramid_max_wallet_pct: float = 0.70
    
    # Risk Limits
    max_positions: int = 4
    max_directional_exposure_usdt: float = 500.0
    trade_usdt_amount: float = 10.0
    # ── Position sizing ──
    # When enabled, size each entry so a stop-loss hit loses ~risk_per_trade_pct
    # of account equity (proper risk-based sizing) instead of a flat $ notional.
    # Falls back to trade_usdt_amount when equity/SL are unavailable.
    equity_sizing_enabled: bool = PydanticField(
        default=True,
        validation_alias=AliasChoices("equity_sizing_enabled", "EQUITY_SIZING_ENABLED"),
    )
    risk_per_trade_pct: float = PydanticField(
        default=0.01,  # risk 1% of equity per trade
        validation_alias=AliasChoices("risk_per_trade_pct", "RISK_PER_TRADE_PCT"),
    )
    # Hard cap on a single trade's notional as a multiple of equity (after
    # leverage). Keeps risk-based sizing from over-allocating on a tight stop.
    max_trade_notional_equity_mult: float = PydanticField(
        default=2.0,
        validation_alias=AliasChoices("max_trade_notional_equity_mult", "MAX_TRADE_NOTIONAL_EQUITY_MULT"),
    )
    kill_floor_usdt: float = PydanticField(
        default=65.0,
        validation_alias=AliasChoices("kill_floor_usdt", "TRADING_KILL_FLOOR_USDT"),
    )
    
    # Safety hard boundaries (Phase 7)
    max_position_risk_pct: float = 1.0
    # Env-tunable so the loop can be tested through a drawdown without a rebuild.
    # Set RISK_MAX_DRAWDOWN_PCT=99 in .env to effectively disable the drawdown
    # halt; default 20.0 preserves the original protection.
    max_portfolio_drawdown_pct: float = PydanticField(
        default=20.0,
        validation_alias=AliasChoices(
            "max_portfolio_drawdown_pct", "RISK_MAX_DRAWDOWN_PCT"
        ),
    )
    max_daily_loss_pct: float = PydanticField(
        default=5.0,
        validation_alias=AliasChoices(
            "max_daily_loss_pct", "RISK_MAX_DAILY_LOSS_PCT"
        ),
    )
    max_open_positions: int = 10
    
    # Signal thresholds (env-tunable so trade aggressiveness can change without a rebuild)
    min_signal_strength: float = PydanticField(
        default=0.45,
        validation_alias=AliasChoices("min_signal_strength", "MIN_SIGNAL_STRENGTH"),
    )
    ai_analysis_threshold: float = PydanticField(
        default=0.30,
        validation_alias=AliasChoices("ai_analysis_threshold", "AI_ANALYSIS_THRESHOLD"),
    )
    opinion_override_margin: float = 0.15
    opinion_entry_min: float = 0.25
    
    # SL/TP (env-tunable so the payoff geometry can be tuned without a rebuild)
    sl_atr_mult: float = PydanticField(
        default=1.0,
        validation_alias=AliasChoices("sl_atr_mult", "SL_ATR_MULT"),
    )
    tp_atr_mult: float = PydanticField(
        default=2.5,
        validation_alias=AliasChoices("tp_atr_mult", "TP_ATR_MULT"),
    )

    # ── Min-edge gate (fee-churn killer) ──
    # 81-day history: 52% of trades were scratches (|PnL| <= $0.20) and
    # commission drag was -$193.54 on a +$64 net. Round-trip taker fee on
    # Binance Futures is ~2 * 0.04% = 0.08% of notional. We only take a
    # trade if its gross expected move to TP clears `min_edge_fee_mult`x the
    # round-trip cost (fees + slippage buffer). This kills tight-TP scratch
    # entries that can never out-earn their own fees. Set 0 to disable.
    taker_fee_rate: float = PydanticField(
        default=0.0004,
        validation_alias=AliasChoices("taker_fee_rate", "TAKER_FEE_RATE"),
    )
    slippage_rate: float = PydanticField(
        default=0.0002,
        validation_alias=AliasChoices("slippage_rate", "SLIPPAGE_RATE"),
    )
    min_edge_fee_mult: float = PydanticField(
        default=2.5,
        validation_alias=AliasChoices("min_edge_fee_mult", "MIN_EDGE_FEE_MULT"),
    )

    @property
    def roundtrip_cost_rate(self) -> float:
        """Round-trip cost as a fraction of notional: entry+exit fee + slippage both sides."""
        return 2.0 * (self.taker_fee_rate + self.slippage_rate)

    # ── Trailing stop (ratchet-only, ATR-based) ──
    # Solves the "trade just oscillates between SL and TP" problem: once a
    # position has moved `trail_activation_atr` x ATR into profit, the stop
    # starts following price by `trail_atr_mult` x ATR and only ever tightens
    # (never loosens). Chop that reverses gets stopped at a locked-in profit /
    # breakeven instead of round-tripping back to the original SL. The trailed
    # level is written into the Trade.stop_loss column, so the existing
    # _check_sl_tp close path enforces it — no separate order needed.
    # Set trailing_stop_enabled=false to disable entirely.
    trailing_stop_enabled: bool = PydanticField(
        default=True,
        validation_alias=AliasChoices("trailing_stop_enabled", "TRAILING_STOP_ENABLED"),
    )
    # ── Exchange-native trailing stop (Binance TRAILING_STOP_MARKET) ──
    # Placed at entry so the stop trails the market continuously on the exchange
    # (tick-by-tick) and keeps protecting profit even if the bot is offline —
    # unlike the software ratchet which only updates once per 15-min cycle.
    # When enabled, the per-cycle software ratchet is skipped to avoid two
    # competing trail mechanisms; the hard STOP_MARKET stays as a catastrophe floor.
    native_trailing_enabled: bool = PydanticField(
        default=True,
        validation_alias=AliasChoices("native_trailing_enabled", "NATIVE_TRAILING_ENABLED"),
    )
    # Binance callbackRate: trail distance as a percent of price (0.1–5.0).
    trailing_callback_rate: float = PydanticField(
        default=1.0,
        validation_alias=AliasChoices("trailing_callback_rate", "TRAILING_CALLBACK_RATE"),
    )
    # Activate the trail only after price moves this fraction in favor (0 = immediate).
    trailing_activation_pct: float = PydanticField(
        default=0.005,
        validation_alias=AliasChoices("trailing_activation_pct", "TRAILING_ACTIVATION_PCT"),
    )
    # How far into profit (in ATR units) before the trail activates.
    trail_activation_atr: float = PydanticField(
        default=1.0,
        validation_alias=AliasChoices("trail_activation_atr", "TRAIL_ACTIVATION_ATR"),
    )
    # Trailing distance behind the high-water mark, in ATR units.
    # MUST be < trail_activation_atr, otherwise the trail locks a LOSS at
    # activation and can only lock +1R once price reaches the TP itself — which
    # caps every realized winner below the 2.5R the min-edge gate sized for and
    # bleeds refunded margin (leak #4). At 0.8 with activation 1.0 the trail
    # locks ~+0.2 ATR profit the moment it arms, and banks ~+1.2R on a 2-ATR
    # push that reverses, so winners >= losers.
    trail_atr_mult: float = PydanticField(
        default=0.8,
        validation_alias=AliasChoices("trail_atr_mult", "TRAIL_ATR_MULT"),
    )

    # ── Symbol-quality gate (liquidity filter + blacklist) ──
    # Reject any symbol whose 24h quote volume (USDT) is below this floor.
    # Low-liquidity / new-listing meme symbols (SIREN, AIGENSYN, MAGMA, SPK ...)
    # produced the entire realized-PnL loss tail (~-$300) via wide spreads,
    # slippage and forced liquidations. Set 0 to disable the volume gate.
    min_24h_quote_volume_usdt: float = PydanticField(
        default=50_000_000.0,
        validation_alias=AliasChoices(
            "min_24h_quote_volume_usdt", "MIN_24H_QUOTE_VOLUME_USDT"
        ),
    )
    # Hard blacklist (comma-separated env). Always skipped regardless of volume.
    symbol_blacklist_raw: str = PydanticField(
        default="",
        validation_alias=AliasChoices("symbol_blacklist", "SYMBOL_BLACKLIST"),
    )

    @property
    def symbol_blacklist(self) -> set[str]:
        return {
            s.strip().upper()
            for s in self.symbol_blacklist_raw.split(",")
            if s.strip()
        }
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

# Global singleton
_risk_config = None

def get_risk_config() -> RiskConfig:
    global _risk_config
    if _risk_config is None:
        _risk_config = RiskConfig()
    return _risk_config

def get_trading_mode() -> str:
    """Resolve trading mode (paper vs. live) based on config/env variables."""
    # If PAPER_TRADING is true or DRY_RUN_ALL is true, it is paper mode
    paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"
    dry_run = os.getenv("DRY_RUN_ALL", "true").lower() == "true"
    return "paper" if (paper_trading or dry_run) else "live"
