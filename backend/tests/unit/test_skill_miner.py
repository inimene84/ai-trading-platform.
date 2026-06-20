"""
Unit tests for the Skill Miner pure functions.
================================================
These cover the mockless, DB-free core: cosine/centroid math, deterministic
clustering, cluster scoring (bullish/bearish/neutral), human naming, the stable
skill_key, and the end-to-end mine_skills pipeline.

Run:
  PYTHONPATH=/home/user/workspace/atp python backend/tests/unit/test_skill_miner.py
"""

import math
import sys

from backend.services.skill_miner import (
    _cosine,
    _normalise,
    _centroid,
    _stdev,
    TradeSample,
    cluster_samples,
    score_cluster,
    name_skill,
    skill_key_for,
    mine_skills,
)

VSIZE = 64
THRESH = 0.80
MIN_CLUSTER = 4

_failures = []


def check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        _failures.append(msg)


# ─────────────────────────────────────────────────────────────────────────────
# math helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_cosine():
    print("test_cosine")
    check(abs(_cosine([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-9, "identical vectors -> 1.0")
    check(abs(_cosine([1, 0], [0, 1])) < 1e-9, "orthogonal vectors -> 0.0")
    check(abs(_cosine([1, 0], [-1, 0]) + 1.0) < 1e-9, "opposite vectors -> -1.0")
    check(_cosine([], [1, 2]) == 0.0, "empty vector -> 0.0")
    check(_cosine([1, 2], [1, 2, 3]) == 0.0, "mismatched length -> 0.0")
    # cosine ignores magnitude
    check(abs(_cosine([2, 0], [5, 0]) - 1.0) < 1e-9, "same direction diff magnitude -> 1.0")


def test_normalise_and_centroid():
    print("test_normalise_and_centroid")
    n = _normalise([3.0, 4.0])
    check(abs(math.sqrt(n[0] ** 2 + n[1] ** 2) - 1.0) < 1e-9, "normalise yields unit length")
    check(_normalise([0.0, 0.0]) == [0.0, 0.0], "zero vector normalises safely")

    check(_centroid([]) == [], "empty centroid -> []")
    cen = _centroid([[1.0, 0.0], [1.0, 0.0]])
    check(abs(cen[0] - 1.0) < 1e-9 and abs(cen[1]) < 1e-9, "centroid of identical -> same dir (unit)")
    # centroid is normalised
    cen2 = _centroid([[1.0, 0.0], [0.0, 1.0]])
    check(abs(math.sqrt(cen2[0] ** 2 + cen2[1] ** 2) - 1.0) < 1e-9, "centroid is unit length")


def test_stdev():
    print("test_stdev")
    check(_stdev([5.0]) == 0.0, "single value -> 0")
    check(_stdev([]) == 0.0, "empty -> 0")
    check(abs(_stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) - 2.138) < 0.01,
          "known sample stdev")


# ─────────────────────────────────────────────────────────────────────────────
# clustering
# ─────────────────────────────────────────────────────────────────────────────

def _sample(symbol, direction, pnl, ctx):
    return TradeSample(symbol=symbol, direction=direction, pnl=pnl, context=ctx)


def test_cluster_groups_similar():
    print("test_cluster_groups_similar")
    # Two distinct archetypes: strong trending-up BUYs vs ranging oversold SELLs.
    up_ctx = {"direction": "BUY", "regime": "TREND_UP", "trend_signal": 1.0,
              "momentum_signal": 1.0, "rsi": 70}
    down_ctx = {"direction": "SELL", "regime": "TREND_DOWN", "trend_signal": -1.0,
                "momentum_signal": -1.0, "rsi": 30}
    samples = []
    for i in range(5):
        samples.append(_sample("BTCUSDT", "BUY", 100.0 + i, dict(up_ctx)))
    for i in range(5):
        samples.append(_sample("ETHUSDT", "SELL", -50.0 - i, dict(down_ctx)))

    clusters = cluster_samples(samples, THRESH, VSIZE)
    check(len(clusters) == 2, f"two archetypes -> two clusters (got {len(clusters)})")
    sizes = sorted(len(c) for c in clusters)
    check(sizes == [5, 5], f"each cluster has 5 trades (got {sizes})")
    # vectors were populated as a side effect
    check(all(len(s.vector) == VSIZE for s in samples), "ensure_vector filled all vectors")


def test_cluster_deterministic():
    print("test_cluster_deterministic")
    ctx = {"direction": "BUY", "regime": "TREND_UP", "trend_signal": 1.0}
    s1 = [_sample("BTCUSDT", "BUY", float(i), dict(ctx)) for i in range(6)]
    s2 = [_sample("BTCUSDT", "BUY", float(i), dict(ctx)) for i in range(6)]
    c1 = cluster_samples(s1, THRESH, VSIZE)
    c2 = cluster_samples(s2, THRESH, VSIZE)
    check(len(c1) == len(c2), "same input -> same cluster count (deterministic)")


# ─────────────────────────────────────────────────────────────────────────────
# scoring
# ─────────────────────────────────────────────────────────────────────────────

def test_score_bullish():
    print("test_score_bullish")
    cluster = [_sample("BTCUSDT", "BUY", p, {}) for p in [10, 20, 30, -5]]
    direction, win_rate, avg_pnl, total_pnl, sharpe, edge = score_cluster(cluster)
    check(direction == "bullish", f"profitable winning cluster -> bullish (got {direction})")
    check(abs(win_rate - 0.75) < 1e-9, f"win_rate 0.75 (got {win_rate})")
    check(abs(total_pnl - 55.0) < 1e-6, f"total_pnl 55 (got {total_pnl})")
    check(0.0 <= edge <= 1.0, f"edge_score in [0,1] (got {edge})")
    check(sharpe is not None, "sharpe computed for >1 sample with variance")


def test_score_bearish():
    print("test_score_bearish")
    cluster = [_sample("ETHUSDT", "SELL", p, {}) for p in [-10, -20, -30, 5]]
    direction, win_rate, avg_pnl, total_pnl, sharpe, edge = score_cluster(cluster)
    check(direction == "bearish", f"losing cluster -> bearish (got {direction})")
    check(avg_pnl < 0, f"avg_pnl negative (got {avg_pnl})")


def test_score_neutral():
    print("test_score_neutral")
    # avg_pnl > 0 but win_rate < 0.5 -> neither bullish nor bearish branch
    cluster = [_sample("BTCUSDT", "BUY", p, {}) for p in [-1, -1, -1, 100]]
    direction, win_rate, avg_pnl, total_pnl, sharpe, edge = score_cluster(cluster)
    check(direction == "neutral", f"mixed cluster -> neutral (got {direction})")


def test_score_zero_variance_sharpe_none():
    print("test_score_zero_variance_sharpe_none")
    cluster = [_sample("BTCUSDT", "BUY", 10.0, {}) for _ in range(4)]
    direction, win_rate, avg_pnl, total_pnl, sharpe, edge = score_cluster(cluster)
    check(sharpe is None, "zero-variance pnl -> sharpe None")
    check(direction == "bullish", "all-wins -> bullish")
    check(abs(win_rate - 1.0) < 1e-9, "win_rate 1.0")


# ─────────────────────────────────────────────────────────────────────────────
# naming + key
# ─────────────────────────────────────────────────────────────────────────────

def test_name_skill():
    print("test_name_skill")
    fsum = {"regime": 1.0, "momentum": 1.0, "rsi": 0.5, "funding": 0.0}
    name = name_skill(fsum, ["BTCUSDT"], "bullish")
    check("Trending-up" in name, "name reflects trending-up regime")
    check("bullish-momentum" in name, "name reflects bullish momentum")
    check("overbought" in name, "name reflects overbought rsi")
    check("bullish" in name, "name includes direction")
    check("[BTCUSDT]" in name, "single symbol appended")

    fsum2 = {"regime": 0.0}
    name2 = name_skill(fsum2, ["BTCUSDT", "ETHUSDT"], "bearish")
    check("Ranging" in name2, "neutral regime -> Ranging")
    check("BTCUSDT" in name2 and "ETHUSDT" in name2, "2-3 symbols listed")

    name3 = name_skill({"regime": 0.0}, ["A", "B", "C", "D"], "neutral")
    check("[" not in name3, "more than 3 symbols -> no symbol tag")


def test_skill_key_stable_and_direction_sensitive():
    print("test_skill_key_stable_and_direction_sensitive")
    cen = _normalise([1.0, 0.5, 0.2] + [0.0] * 8)
    k1 = skill_key_for(cen, "bullish")
    k2 = skill_key_for(cen, "bullish")
    check(k1 == k2, "same centroid+direction -> same key (deterministic)")
    check(len(k1) == 16, "key is 16 hex chars")
    k3 = skill_key_for(cen, "bearish")
    check(k1 != k3, "different direction -> different key")
    # tiny drift within a 0.25 bucket -> same key
    cen_drift = _normalise([1.0, 0.51, 0.2] + [0.0] * 8)
    check(skill_key_for(cen, "bullish") == skill_key_for(cen_drift, "bullish"),
          "small drift quantised to same key")


# ─────────────────────────────────────────────────────────────────────────────
# end-to-end mine
# ─────────────────────────────────────────────────────────────────────────────

def test_mine_skills_respects_min_cluster():
    print("test_mine_skills_respects_min_cluster")
    up_ctx = {"direction": "BUY", "regime": "TREND_UP", "trend_signal": 1.0,
              "momentum_signal": 1.0}
    # 5 similar BUYs (forms a skill) + 2 lone SELLs (below min cluster size)
    samples = [_sample("BTCUSDT", "BUY", 100.0 + i, dict(up_ctx)) for i in range(5)]
    samples += [_sample("XRPUSDT", "SELL", -10.0,
                        {"direction": "SELL", "regime": "TREND_DOWN", "trend_signal": -1.0})
                for _ in range(2)]
    mined = mine_skills(samples, min_cluster_size=MIN_CLUSTER,
                        threshold=THRESH, vector_size=VSIZE)
    check(len(mined) == 1, f"only the >=4 cluster becomes a skill (got {len(mined)})")
    sk = mined[0]
    check(sk.sample_count == 5, f"skill has 5 samples (got {sk.sample_count})")
    check(sk.direction == "bullish", f"profitable cluster -> bullish (got {sk.direction})")
    check(sk.symbols == ["BTCUSDT"], "symbols deduped + sorted")
    check(0.0 <= sk.edge_score <= 1.0, "edge_score bounded")
    check(len(sk.centroid) == VSIZE, "centroid full width")
    check(isinstance(sk.to_dict(), dict), "to_dict serialises")


def test_mine_skills_sorted_by_edge():
    print("test_mine_skills_sorted_by_edge")
    up_ctx = {"direction": "BUY", "regime": "TREND_UP", "trend_signal": 1.0, "momentum_signal": 1.0}
    down_ctx = {"direction": "SELL", "regime": "TREND_DOWN", "trend_signal": -1.0, "momentum_signal": -1.0}
    # cluster A: very consistent winners (high edge); cluster B: noisier
    samples = [_sample("BTCUSDT", "BUY", 100.0, dict(up_ctx)) for _ in range(8)]
    samples += [_sample("ETHUSDT", "SELL", v, dict(down_ctx)) for v in [-10, -8, 5, -3]]
    mined = mine_skills(samples, min_cluster_size=MIN_CLUSTER, threshold=THRESH, vector_size=VSIZE)
    check(len(mined) >= 1, "at least one skill mined")
    if len(mined) >= 2:
        check(mined[0].edge_score >= mined[1].edge_score, "skills sorted by edge_score desc")


def test_mine_skills_empty():
    print("test_mine_skills_empty")
    check(mine_skills([], min_cluster_size=MIN_CLUSTER, threshold=THRESH, vector_size=VSIZE) == [],
          "no samples -> no skills")


def main():
    tests = [
        test_cosine,
        test_normalise_and_centroid,
        test_stdev,
        test_cluster_groups_similar,
        test_cluster_deterministic,
        test_score_bullish,
        test_score_bearish,
        test_score_neutral,
        test_score_zero_variance_sharpe_none,
        test_name_skill,
        test_skill_key_stable_and_direction_sensitive,
        test_mine_skills_respects_min_cluster,
        test_mine_skills_sorted_by_edge,
        test_mine_skills_empty,
    ]
    for t in tests:
        t()
    print()
    if _failures:
        print(f"{len(_failures)} CHECK(S) FAILED:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"All {len(tests)} test functions passed.")


if __name__ == "__main__":
    main()
