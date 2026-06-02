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
    kill_floor_usdt: float = PydanticField(
        default=65.0,
        validation_alias=AliasChoices("kill_floor_usdt", "TRADING_KILL_FLOOR_USDT"),
    )
    
    # Safety hard boundaries (Phase 7)
    max_position_risk_pct: float = 1.0
    max_portfolio_drawdown_pct: float = 20.0
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 10
    
    # Signal thresholds
    min_signal_strength: float = 0.60
    ai_analysis_threshold: float = 0.60
    opinion_override_margin: float = 0.15
    opinion_entry_min: float = 0.25
    
    # SL/TP
    sl_atr_mult: float = 1.0
    tp_atr_mult: float = 2.5
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
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
