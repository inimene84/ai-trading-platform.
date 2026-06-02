import re

file_path = 'backend/services/trading_loop.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

parts = re.split(r'(async def _process_symbol.*?)(?=    async def _run_cycle_OLD|    def _save_portfolio_snapshot)', content, flags=re.DOTALL)

if len(parts) >= 3:
    new_method = '''async def _process_symbol(self, symbol: str, min_confidence: float, ai_threshold: float, max_positions: int):
        from backend.services.decision_engine import DecisionEngine
        from backend.database.connection import SessionLocal
        from backend.database.models import Trade, Signal
        from datetime import datetime, timezone
        from backend.services.unified_trading import UnifiedTrading, UnifiedOrder, OrderSide, OrderType
        import asyncio

        db = SessionLocal()
        _signals = 0
        _trades = 0

        try:
            # 1. Fetch existing position
            existing = db.query(Trade).filter(
                Trade.symbol == symbol,
                Trade.status.in_(["open", "filled"])
            ).first()

            # 2. Fetch bars
            bars = await self._fetch_bars(symbol)
            if not bars or len(bars) < 50:
                return {"signals": 0, "trades": 0}

            if not self._is_market_open(symbol):
                return {"signals": 0, "trades": 0}

            # 3. Check cooldown
            cooldown_active = False
            if symbol in self._sl_cooldown:
                elapsed = (datetime.now(timezone.utc) - self._sl_cooldown[symbol]).total_seconds() / 60
                if elapsed < getattr(self.risk_config, "opinion_close_cooldown_min", 30):
                    cooldown_active = True
                else:
                    del self._sl_cooldown[symbol]

            # 4. Evaluate using Decision Engine
            decision_engine = DecisionEngine(self.risk_config)
            decision = await decision_engine.evaluate_symbol(
                symbol=symbol,
                bars=bars,
                existing_position=existing,
                open_count=self._open_count,
                pyramid_layers=self._pyramid_layers.get(symbol, []),
                cooldown_active=cooldown_active
            )

            if decision:
                _signals = 1
                
                # Check directional exposure limit (if not pyramid)
                _new_notional = decision.quantity * decision.entry_price
                if not decision.is_pyramid:
                    all_open = db.query(Trade).filter(Trade.status.in_(["open", "filled"])).all()
                    _long_notional = sum(t.quantity * (t.entry_price or 0.0) for t in all_open if t.direction == "BUY")
                    _short_notional = sum(t.quantity * (t.entry_price or 0.0) for t in all_open if t.direction == "SELL")
                    
                    if decision.action == "BUY" and (_long_notional + _new_notional > self.risk_config.max_directional_exposure_usdt):
                        self.logger.warning(f"  [ {symbol} ] BUY blocked: LONG exposure cap reached")
                        decision = None
                    elif decision.action == "SELL" and (_short_notional + _new_notional > self.risk_config.max_directional_exposure_usdt):
                        self.logger.warning(f"  [ {symbol} ] SELL blocked: SHORT exposure cap reached")
                        decision = None

            if decision:
                # 5. Execute Order
                ut = UnifiedTrading()
                order_side = OrderSide.BUY if decision.action == "BUY" else OrderSide.SELL
                
                self.logger.info(f"  [ {symbol} ] Attempting {decision.action} order: qty={decision.quantity:.6f} @ {decision.entry_price} | SL={decision.stop_loss} TP={decision.take_profit}")
                
                order = UnifiedOrder(
                    symbol=symbol, side=order_side, quantity=decision.quantity, price=decision.entry_price,
                    stop_loss=decision.stop_loss, take_profit=decision.take_profit
                )
                
                res = await asyncio.get_event_loop().run_in_executor(None, lambda: ut.place_order(order))
                if res.success:
                    if not existing:
                        self._open_count += 1
                    _trades = 1
                    self.logger.info(f"  [ {symbol} ] SUCCESS: {res.order_id} filled @ {res.filled_price}")
                    
                    if decision.is_pyramid:
                        self._pyramid_layers.setdefault(symbol, []).append(float(res.filled_price or decision.entry_price))
                    
                    trade = Trade(
                        symbol=symbol, direction=decision.action, quantity=res.filled_qty or decision.quantity,
                        entry_price=res.filled_price or decision.entry_price, status="open",
                        strategy=self._strategy_name,
                        binance_order_id=res.order_id,
                        stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                        notes=f"pyramid_layer_{len(self._pyramid_layers.get(symbol, []))}" if decision.is_pyramid else None
                    )
                    db.add(trade)
                    db.commit()
                else:
                    self.logger.warning(f"  [ {symbol} ] FAILED: {res.message}")

            # SL/TP Check
            self._check_sl_tp(db, symbol, bars)
            db.commit()

            return {"signals": _signals, "trades": _trades}

        except Exception as e:
            self.logger.error(f"Error processing {symbol}: {e}")
            db.rollback()
            return {"signals": 0, "trades": 0}
        finally:
            db.close()
            
'''
    new_content = parts[0] + new_method + parts[2]
    
    # Also add import for RiskConfig in __init__
    if "self.risk_config =" not in new_content:
        new_content = new_content.replace(
            'self._enable_personas = os.getenv("ENABLE_PERSONAS", "true").lower() == "true"',
            'self._enable_personas = os.getenv("ENABLE_PERSONAS", "true").lower() == "true"\n        from backend.services.risk_config import get_risk_config\n        self.risk_config = get_risk_config()'
        )
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Successfully replaced _process_symbol")
else:
    print("Could not find matching _process_symbol block")
