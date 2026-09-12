"""USDT / USDC (and BUSD) crypto-perp aliases for one BTC/ETH/… book.

Live opinions and loop symbols can disagree on quote (BTCUSDC vs BTCUSDT)
while the venue book is a single USDT-M leg. Flatten, admission, and
one-per-symbol checks must treat those quotes as the same instrument.
FX pairs (USDCAD, EURUSD) are never aliased.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

_STABLE_QUOTES = ("USDT", "USDC", "BUSD")
_FLATTEN_ACTIONS = frozenset({"CLOSE_LONG", "CLOSE_SHORT"})


def normalize_side(direction: Optional[str]) -> str:
    side = str(direction or "").upper()
    if side in ("BUY", "LONG"):
        return "BUY"
    if side in ("SELL", "SHORT"):
        return "SELL"
    return side


def is_flatten_action(action: Optional[str]) -> bool:
    return str(action or "").upper() in _FLATTEN_ACTIONS


def is_opposing_side(signal_side: Optional[str], position_side: Optional[str]) -> bool:
    left = normalize_side(signal_side)
    right = normalize_side(position_side)
    return {left, right} == {"BUY", "SELL"}


def crypto_perp_base(symbol: Optional[str]) -> Optional[str]:
    """BTCUSDT / BTC-USD / BTC/USDC → BTC. FX and bare names return None."""
    s = str(symbol or "").upper().replace("-", "").replace("/", "")
    for quote in _STABLE_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return None


def perp_alias_set(symbol: Optional[str]) -> set[str]:
    """Exact symbol plus USDT/USDC/BUSD twins when it is a crypto perp."""
    raw = str(symbol or "").upper()
    aliases = {raw} if raw else set()
    base = crypto_perp_base(raw)
    if base:
        aliases.update(f"{base}{q}" for q in _STABLE_QUOTES)
    return aliases


def same_crypto_perp_leg(left: Optional[str], right: Optional[str]) -> bool:
    if not left or not right:
        return False
    a = str(left).upper()
    b = str(right).upper()
    if a == b:
        return True
    base_a = crypto_perp_base(a)
    base_b = crypto_perp_base(b)
    return bool(base_a and base_b and base_a == base_b)


def first_matching_open(
    rows: Iterable[Any],
    symbol: str,
    *,
    symbol_attr: str = "symbol",
) -> Any:
    """Prefer an exact symbol hit, else the first same-perp-leg row."""
    wanted = str(symbol or "").upper()
    exact = None
    alias = None
    for row in rows:
        if isinstance(row, dict):
            row_sym = str(row.get(symbol_attr) or "").upper()
        else:
            row_sym = str(getattr(row, symbol_attr, "") or "").upper()
        if not row_sym:
            continue
        if row_sym == wanted:
            exact = row
            break
        if alias is None and same_crypto_perp_leg(row_sym, wanted):
            alias = row
    return exact if exact is not None else alias


def stop_on_correct_side(
    direction: Optional[str],
    stop: Optional[float],
    *,
    mark: Optional[float] = None,
    entry: Optional[float] = None,
) -> bool:
    """Protective stop must sit on the loss side of mark (and entry when given).

    Long: stop < mark and (if entry) stop <= entry is allowed for BE locks
    that are still below mark. Short: stop > mark. A short stop below mark
    or a long stop above mark would fire immediately — refuse it.
    """
    try:
        level = float(stop) if stop is not None else 0.0
    except (TypeError, ValueError):
        return False
    if level <= 0:
        return False
    side = normalize_side(direction)
    mark_px = None
    entry_px = None
    try:
        if mark is not None and float(mark) > 0:
            mark_px = float(mark)
    except (TypeError, ValueError):
        mark_px = None
    try:
        if entry is not None and float(entry) > 0:
            entry_px = float(entry)
    except (TypeError, ValueError):
        entry_px = None

    if side == "BUY":
        if mark_px is not None and level >= mark_px:
            return False
        if entry_px is not None and mark_px is None and level > entry_px:
            return False
        return True
    if side == "SELL":
        if mark_px is not None and level <= mark_px:
            return False
        if entry_px is not None and mark_px is None and level < entry_px:
            return False
        return True
    return False
