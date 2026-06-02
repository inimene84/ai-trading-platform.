import os
from pydantic_settings import BaseSettings

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
    kill_floor_usdt: float = 65.0
    
    # Signal thresholds
    min_signal_strength: float = 0.60
    ai_analysis_threshold: float = 0.60
    opinion_override_margin: float = 0.15
    opinion_entry_min: float = 0.25
    
    # SL/TP
    sl_atr_mult: float = 1.0
    tp_atr_mult: float = 2.5
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Map env vars to attributes where names don't exactly match
        fields = {
            'kill_floor_usdt': {'env': 'TRADING_KILL_FLOOR_USDT'}
        }

# Global singleton
_risk_config = None

def get_risk_config() -> RiskConfig:
    global _risk_config
    if _risk_config is None:
        _risk_config = RiskConfig()
    return _risk_config
