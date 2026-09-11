# Why the Jesse LightGBM directional model collapses to one class

**Verdict: the hypothesis holds.** The collapse is label degeneracy created by the
5.5 ATR / 1.75 ATR barrier geometry, not a model or hyperparameter defect. The
decisive number: on the rejected model's own holdout the majority class is
**66.94 %** and the model scored **66.13 %** — it is **0.81 pp worse than a
constant predictor**. The earlier every-bar artifact is the same story to the
bar: majority 59.7024 %, model 59.6825 %, i.e. it got exactly **one** bar out of
5,040 fewer right than "always say bearish".

Everything below comes from `scripts/research/label_study.py` run against real
candles pulled out of `jesse-postgres`. Raw output is in
`docs/research/label_study.json`. No model was trained, no backtest or optimizer
was run, no container was restarted, and nothing on the VPS was written outside
`/tmp/labelstudy/`.

---

## 1. Method and data provenance

**Candles.** 1-minute bars from the `candle` table (`jesse_db`, Binance
Perpetual Futures) were rolled up to 1h **inside Postgres**
(`scripts/research/extract_1h.sh`) so no large frame was ever materialised in
Python on the production host. Aggregation is `first(open) / max(high) /
min(low) / last(close) / sum(volume)` per `floor(ts/3600000)` bucket, which is
what `train_ml.load_candles_from_db` produces via `df.resample("1h")`.

**Windows used** (contiguous, most recent, exactly as reported):

| symbol | 1h bars used | window (UTC) | note |
|---|---|---|---|
| BTC-USDT | 8,000 | 2025-10-10 16:00 → 2026-09-08 23:00 | 25,248 available; full history used only for the reconciliation in §2 |
| ETH-USDT | 8,000 | 2025-10-11 16:00 → 2026-09-09 23:00 | backfill had reached 14,810 bars by the time of extraction |
| SOL-USDT | 960 | 2026-08-01 00:00 → 2026-09-09 23:00 | **only 960 bars exist.** Every SOL figure is small-sample; its meta-label row at the 336-bar horizon rests on 62 events. Treat as indicative only. |

**Labelling.** The study uses a vectorised re-implementation of
`triple_barrier.apply_triple_barrier` rather than the original Python loop, for
speed at long horizons. It was checked against the repo's own function on an
unmodified copy of `/root/jesse-trading/triple_barrier.py` and is **exact**:

```
geometry 5.5/1.75, horizon 24, 1,976 events (BTC), 1,976 (ETH), 936 (SOL)
label mismatches ............ 0
touch_type mismatches ....... 0
max |ret| difference ........ 0.0
max |trgt| difference ....... 0.0
```

The re-implementation preserves the two semantics that matter: a bar in which
both barriers fall is assumed to hit the **stop** first, and a vertical-barrier
expiry is **not** labelled 0 — it is re-labelled ±1 unless `|return| <= 0.2 *
sl_mult * ATR`. That second rule turns out to matter a great deal (§6).

**Resource safety.** Postgres-side aggregation plus `scp` was the only work done
on the VPS; the analysis ran off-box. Available RAM measured before and after
every step stayed in the 7.9–8.2 GB band (`SwapFree` 138–220 MB throughout, i.e.
swap was already exhausted and stayed that way — nothing this study did moved
it). `git status` in `/root/jesse-trading` shows only the other agent's model
artifacts; no file of mine.

---

## 2. The decisive number: model vs. its own majority baseline

The recorded metrics in `*_rejected_meta.json` are float64 ratios of small
integers, so the confusion matrix is exactly recoverable. Searching for integer
counts that reproduce **every** reported float bit-for-bit yields a unique
solution (`reconstruct_confusion()` in the script):

| | predicted positive | predicted negative | total |
|---|---|---|---|
| **true positive class** | 4 | 119 | **123** |
| **true negative class** | 7 | 242 | **249** |
| total | 11 | 361 | **372** |

This reproduces `bullish_recall` = 4/123, `bullish_precision` = 4/11,
`bearish_recall` = 242/249, `bearish_precision` = 242/361 and `test_accuracy` =
246/372 = 0.6612903225806451 — all to full float precision.

I then rebuilt that label vector **from raw candles**, independently. The
rejected model was trained on QuantumAI-filtered long entries (event sampling),
5.5/1.75, 24-bar horizon, 80/20 split:

```
BTC-USDT full history, 25,248 1h bars
QuantumAI long events ................ 1,861
labelled events (horizon-truncated) .. 1,860   -> 20 % holdout = 372   [matches]
last 372 events: positive = 123, negative = 249                        [matches exactly]
```

So the reconstruction is not an inference; it is the same label vector.

**The comparison:**

| | rejected model (event-sampled, 2-class) | earlier `collapsed-everybar` (every bar, 3-class) |
|---|---|---|
| holdout size | 372 | 5,040 |
| majority class share | **0.669355** (249/372) | **0.597024** (3009/5040) |
| model accuracy | **0.661290** (246/372) | **0.596825** (3008/5040) |
| **accuracy − majority** | **−0.81 pp** | **−0.02 pp (one bar)** |

Both models are *worse than a constant*. The `collapsed-everybar` 3-class split
recomputed from candles is 1,793 / 3,009 / 238 — the 3,009 matches the count
implied by its recorded `bearish_recall` denominator exactly.

The user's prediction that "accuracy equal to the majority-class share would be
strong confirmation" is satisfied, and then some: accuracy is not merely equal
to the majority share, it is marginally *below* it.

---

## 3. Label distribution vs. barrier geometry (horizon = 24 bars)

Three-way `touch_type` outcome, i.e. which barrier was reached first:

**BTC-USDT, 8,000 bars**

| pt / sl | payoff | up first | down first | timeout | trainer class 1 / 2 / 0 | majority |
|---|---|---|---|---|---|---|
| **5.5 / 1.75** | 3.14 | **9.5 %** | **57.5 %** | 33.0 % | 33.5 / 61.1 / 5.4 | **61.1 %** |
| 4.0 / 2.0 | 2.00 | 18.5 % | 51.5 % | 30.0 % | 36.8 / 56.2 / 7.0 | 56.2 % |
| 1.5 / 1.5 | 1.00 | 46.0 % | 48.3 % | 5.7 % | 48.3 / 49.4 / 2.3 | **49.4 %** |
| 2.0 / 2.0 | 1.00 | 42.7 % | 44.1 % | 13.2 % | 47.8 / 47.1 / 5.1 | **47.8 %** |
| 3.0 / 2.0 | 1.50 | 28.3 % | 48.9 % | 22.8 % | 40.6 / 52.9 / 6.4 | 52.9 % |
| 2.0 / 3.0 | 0.67 | 46.7 % | 31.4 % | 21.9 % | 51.3 / 39.1 / 9.7 | 51.3 % |
| 3.0 / 3.0 | 1.00 | 30.9 % | 34.9 % | 34.2 % | 42.9 / 44.6 / 12.5 | 44.6 % |
| 4.0 / 4.0 | 1.00 | 21.4 % | 25.2 % | 53.4 % | 38.9 / 41.0 / 20.1 | 41.0 % |
| 5.5 / 5.5 | 1.00 | 12.8 % | 13.4 % | 73.8 % | 34.6 / 36.4 / 28.9 | 36.4 % |

**ETH-USDT, 8,000 bars** — same shape throughout: 5.5/1.75 gives 10.6 / 56.2 /
33.2 (majority 59.5 %); 2.0/2.0 gives 43.4 / 43.2 / 13.5 (majority 48.8 %).

**SOL-USDT, 960 bars** — directionally the same but shifted by a strong bull
window: 5.5/1.75 gives 19.2 / 48.7 / 32.1 (majority 51.8 %).

Read across the table: **the skew tracks the payoff ratio, not the model.**
Every symmetric geometry (1.5/1.5, 2/2, 3/3, 4/4, 5.5/5.5) lands within a few
points of 50/50 between up-first and down-first; every asymmetric one skews in
proportion to the asymmetry, and the inverted 2/3 skews the other way. Nothing
about LightGBM is involved in producing that pattern.

---

## 4. Horizon sensitivity — and the reachability of 5.5 ATR

At the deployed 5.5/1.75 geometry, varying the maximum holding period:

**BTC-USDT**

| horizon | up first | down first | timeout | up-first given a barrier was touched | trainer majority |
|---|---|---|---|---|---|
| 12 | 4.5 % | 42.9 % | 52.7 % | 9.44 % | 51.9 % |
| **24 (deployed)** | **9.5 %** | **57.5 %** | **33.0 %** | **14.22 %** | **61.1 %** |
| 48 | 16.3 % | 66.4 % | 17.3 % | 19.67 % | 67.6 % |
| 96 | 22.2 % | 72.7 % | 5.1 % | 23.37 % | 73.1 % |
| 168 | 23.6 % | 74.8 % | 1.6 % | 24.00 % | 74.9 % |
| 336 | 24.1 % | 75.3 % | 0.6 % | 24.27 % | 75.3 % |

**This is the most important table in the study, and it contains a surprise:
lengthening the horizon makes the class balance *worse*, not better.** The
majority share climbs monotonically from 51.9 % at 12 bars to 75.3 % at 336.
ETH is identical (50.1 % → 74.9 %).

The reason is visible in the last-but-one column. For a driftless price the
up barrier is touched first with probability `sl / (pt + sl)` = 1.75 / 7.25 =
**24.14 %**, which is also the break-even win rate at a 3.14 payoff. BTC's
empirical up-first-given-touched rate converges to **24.27 %** at 336 bars and
ETH's to **24.68 %** — within 0.15 pp of the martingale value. Extending the
horizon does not add information; it just removes the timeout bucket and lets
the labels settle onto the number the geometry already implied.

**Is 5.5 ATR reachable within 24 bars?** Mostly not. Maximum favourable
excursion (barrier-independent: how far price actually travels up within the
horizon, in ATR units), BTC:

| horizon | MFE p50 | p75 | p90 | p95 | **P(MFE ≥ 5.5 ATR)** | P(MAE ≥ 1.75 ATR) | P(neither reachable) |
|---|---|---|---|---|---|---|---|
| 12 | 1.34 | 2.58 | 4.25 | 5.60 | **5.3 %** | 43.1 % | 52.7 % |
| **24** | 2.03 | 3.75 | 6.20 | 8.24 | **13.0 %** | 58.4 % | 33.0 % |
| 48 | 2.90 | 5.66 | 9.34 | 12.09 | 26.1 % | 69.0 % | 17.3 % |
| 96 | 4.22 | 7.96 | 11.62 | 15.21 | 40.9 % | 79.0 % | 5.1 % |
| 168 | 5.65 | 9.62 | 14.75 | 20.29 | 51.0 % | 85.1 % | 1.6 % |
| 336 | 7.28 | 12.85 | 21.95 | 42.98 | 59.8 % | 88.9 % | 0.6 % |

At the deployed 24-bar horizon the target is **physically reachable in only
13.0 % of windows**, while the stop is reachable in 58.4 %. Median MFE is
2.03 ATR — the target is 2.7× further than the typical best move available.
Only at a 168-bar horizon does the median window even contain a 5.5 ATR
excursion.

**Bars-to-touch for the up barrier** (BTC, measured at a 336-bar horizon so the
distribution is not truncated; 1,849 up-first events = 24.1 % of all events):
p10 = 8, p25 = 16, **p50 = 33**, p75 = 64, p90 = 94 bars. ETH is the same
(p50 = 32). **The median winner needs 33 bars — the trainer gives it 24.** The
24-bar cutoff therefore does not just shorten the sample, it *selectively
discards the slower winners*, which is why up-first-given-touched is 14.2 % at
h = 24 against the 24.14 % the geometry implies. Roughly 40 % of the eventual
winners are censored away by the vertical barrier.

---

## 5. ATR scale — how big is 5.5 ATR?

| symbol | ATR/price p5 | p25 | p50 | p75 | p95 | mean | **5.5 ATR at median** | 5.5 ATR at p95 |
|---|---|---|---|---|---|---|---|---|
| BTC-USDT | 0.25 % | 0.45 % | 0.62 % | 0.82 % | 1.21 % | 0.66 % | **3.42 %** | 6.66 % |
| ETH-USDT | 0.35 % | 0.62 % | 0.84 % | 1.12 % | 1.69 % | 0.92 % | **4.65 %** | 9.31 % |
| SOL-USDT | 0.35 % | 0.50 % | 0.68 % | 1.09 % | 1.69 % | 0.85 % | **3.72 %** | 9.28 % |

So 5.5 ATR is not the 12 % strawman — it is a **3.4 % move for BTC** at the
median, against a **1.09 % stop**. That is a perfectly ordinary trade *target*.
The problem is not that the target is absurd in isolation; it is the
combination of (i) a 3.14:1 distance ratio, which caps the up-first rate at
24.14 % no matter what, and (ii) a 24-bar window that only contains a 3.4 %
upswing 13 % of the time. A 3.4 % BTC move inside 24 hours is uncommon; a 1.09 %
drawdown inside 24 hours is the norm.

---

## 6. Two defects in how the labels are built (beyond the geometry)

**6a. The positive class is mostly not a win.** `train_ml.prepare_dataset` keys
the positive class off `tb_df["label"] == 1`, not off `touch_type == "pt"`. But
`apply_triple_barrier` re-labels a timed-out window as +1 whenever its return
merely exceeds `0.2 * sl_mult * ATR` = 0.35 ATR ≈ 0.22 % for BTC. On the
rejected model's own 372-bar holdout:

```
123 "positive" labels
 └─  37 actually reached the 5.5 ATR target
 └─  86 (70 %) only timed out above a 0.35 ATR drift threshold
mean realised return: 2.70 % for genuine target hits, 1.22 % for drift positives
```

The model is being asked to predict a target that conflates "this trade makes
+5.5 ATR" with "price drifted up 0.2 % and we gave up" — two outcomes the live
book handles completely differently. The same holds on the every-bar set: of
1,793 positives, only 507 touched the target and 1,286 were drift.

**6b. `holdout_sharpe` is not a Sharpe ratio and must not be trusted.** The
current `train_ml.py` builds it as

```python
pred_signal   = np.where(preds == 1, 1.0, -1.0)
actual_signal = np.where(y_test.to_numpy() == 1, 1.0, -1.0)
rets          = pred_signal * actual_signal * 0.01     # ±1 % on sign agreement
holdout_sr    = calculate_sharpe_ratio(rets)           # × sqrt(365)
```

(the previous 3-class version was the same idea with a third `0.0` arm for the
neutral class).

There is no price, no barrier return, and no position size in it. It is a
monotone rescaling of the hit rate, `sqrt(365) · u / sqrt(1 − u²)` with
`u = 2·hitrate − 1` — so it rewards *agreement with the majority label* exactly
as much as it rewards skill. Confirmation that this is precisely what happened:
inverting the formula on the reported `holdout_sharpe` = 3.224986454918626
recovers **217 agreements out of 372 = 58.33 %** for the best grid trial, a
number below its own 66.94 % majority baseline.

Running the repo's own `validation_metrics` on constant predictors:

| predictor on the rejected model's 372-bar holdout | hit rate | `holdout_sharpe` | DSR |
|---|---|---|---|
| best grid trial (what was recorded) | 58.33 % | 3.2250 | 0.9885 (recorded) |
| final calibrated pipeline | 66.13 % | 6.5022 | 1.0000 |
| **always predict the majority class** | **66.94 %** | **6.8683** | **1.0000** |

**A constant predictor scores more than double the recorded Sharpe and passes
the DSR > 0.95 promotion gate outright.** The same is true of the every-bar
artifact (constant: Sharpe 3.7787 vs. model 3.7707), which is why
`promotion_ok: true` was written into a metadata file for a model with
`bullish_recall: 0.0`. The gate in `promotion_gates.evaluate_promotion` checks
DSR, PBO and geometry, and none of those three can detect a degenerate
classifier.

---

## 7. Is there anything to learn at all?

Descriptive only — no model was fitted. Up-first rate by quintile of a few cheap
indicators, BTC at 5.5/1.75:

| feature | h = 24 (base 9.53 %) | h = 96 (base 22.18 %) | spread at h = 96 |
|---|---|---|---|
| `close / ema50` | 5.6 / 10.0 / 14.0 / 12.3 / 5.8 | 16.0 / 26.2 / **27.5** / 25.3 / 15.9 | 11.7 pp |
| `atr / price` | 16.8 / 9.3 / 6.9 / 8.2 / 6.5 | **30.7** / 20.4 / 21.5 / 21.8 / 16.5 | 14.2 pp |
| `ret_24` | 5.5 / 10.8 / 12.7 / 12.2 / 6.6 | 16.9 / **26.6** / 25.4 / 24.2 / 18.0 | 9.7 pp |
| `rsi_14` | 7.6 / 8.5 / 9.2 / 11.4 / 10.9 | 19.4 / 22.2 / 23.8 / 24.2 / 21.3 | 4.8 pp |

ETH is stronger on the same features (`close/ema50` spans 14.3 → 33.4 % at
h = 96, a 19.1 pp spread).

**There is conditional structure, and it is economically meaningful — but it can
never be the argmax.** The best BTC quintile at h = 96 is 27.5 % up-first
against a 24.14 % break-even: profitable, yet still a 1-in-4 event. An
accuracy-maximising 3-class or 2-class classifier will *never* emit that class,
because "not up" is the correct call 72.5 % of the time even inside the best
bucket. The information is there; the objective function forbids the model from
acting on it. That single sentence is the whole diagnosis.

---

## 8. Remedies, evaluated against the evidence

### (a) Class weighting / balanced sampling — **already in place, already failed**

`_make_clf()` sets `class_weight="balanced"` on every LightGBM, RandomForest and
HistGradientBoosting path, and `sample_weight` from
`compute_sample_uniqueness()` is passed on top. The rejected model collapsed
*with balanced weighting active*. That is decisive evidence against (a) being
the fix.

Why it failed is worth knowing, because it also names a second bug: the final
artifact is wrapped in `CalibratedClassifierCV(method="isotonic", cv=3)`.
Isotonic regression fitted on a minority class this small, split across 3 folds,
produces a near-flat step function, so the calibrated `predict()` argmax can
collapse even when the underlying booster does not. The recorded numbers point
that way. The uncalibrated best grid trial disagreed with the label on 155 of
372 bars; solving `agreement = 249 + 2·TP − P` for its predicted-positive count
gives `P = 32 + 2·TP`, so it emitted **at least 32 positives and possibly far
more**, against the calibrated pipeline's **11**. The booster was still calling
positives, badly; the calibration wrapper is what silenced them.

*Expected effect:* none on its own. *Code:* n/a. *Risk:* continuing to believe
it is a remedy wastes retraining cycles.

### (b) Longer label horizon — **necessary, but not for the reason assumed, and not sufficient**

§4 shows a longer horizon makes the *balance worse* (majority 61.1 % → 75.3 %).
What it does fix is **censoring**: the median winner takes 33 bars, so a 24-bar
window throws away ~40 % of genuine winners and drives up-first-given-touched
down to 14.2 % from the 24.14 % the geometry supports. A 96-bar horizon recovers
it to 23.4 % and cuts the unreachable-target fraction from 87 % to 59 %.

*Expected effect:* labels become *honest* (up-first-given-touched ≈ break-even)
without becoming balanced. Required as an input to (d), useless alone.
*Code:* `--holding 96` and the `max_holding_bars=24` default in
`triple_barrier.apply_triple_barrier` / `generate_meta_labels`.
*Risk:* label overlap explodes — with `t1` spanning 96 bars, purging becomes
load bearing rather than cosmetic. **And there is currently nothing to purge
with:** `PurgedKFold` is imported at line 29 of `train_ml.py` and never called.
`train_model()` does a single 80/20 chronological split and writes
`"cv_mean_accuracy": float(test_acc)` — which is why both artifacts record
`cv_mean_accuracy` exactly equal to `test_accuracy`. There is no
cross-validation in the current trainer at all, purged or otherwise. If the
horizon is lengthened, purged CV with an embargo at least as long as the label
span has to be restored first, or the overlap will manufacture leakage on top
of the degeneracy.

### (c) Symmetric label geometry decoupled from execution geometry — **works mechanically, but breaks the alignment the repo just fixed**

§3 shows 2.0/2.0 yields 42.7 / 44.1 / 13.2 on BTC (majority 47.8 %) and 1.5/1.5
yields 46.0 / 48.3 / 5.7 (majority 49.4 %). A three-class directional model
trained on 2/2 would have a genuinely balanced problem and would not collapse.

The cost: the model then predicts a trade the book never takes, which is exactly
the defect `promotion_gates.geometry_matches_live()` was written to block, and
the reason the 4.0/2.0 models were discarded. You would be re-introducing it
deliberately. It is defensible *only* if the model's output is treated as a
direction prior feeding a separate sizing/qualification stage — at which point
you have built (d) with extra steps.

*Expected effect:* balanced labels, learnable problem, no direct mapping to PnL.
*Code:* `--pt-mult 2 --sl-mult 2`, plus relaxing `require_geometry`.
*Risk:* a well-calibrated model of the wrong question.

### (d) Meta-labelling — **the right call, with one correction**

This is where the evidence points, but with an important caveat: **the rejected
model was already a meta-labelled binary model and it still collapsed.** The
current `prepare_dataset` event-samples on `quantum_ai_event_index()` and emits
a 2-class `P(setup works)`. So "switch to meta-labelling" is not the fix by
itself — the fix is meta-labelling *done correctly*, which means three changes
it is currently missing:

1. **Label on `touch_type == "pt"`, not on `label == 1`.** Per §6a, 70 % of the
   current positives never touched the target. The positive class must mean
   "this trade would have closed at the profit target", full stop.
2. **Lengthen the horizon to ~96 bars.** Measured meta-label positive rates on
   the QuantumAI long events, BTC (break-even = 24.14 %):

   | horizon | events | meta-label + rate | stopped | timed out | expectancy (ATR/trade) |
   |---|---|---|---|---|---|
   | 12 | 450 | 3.56 % | 43.3 % | 53.1 % | −0.094 |
   | **24 (current)** | 449 | **9.35 %** | 60.6 % | 30.1 % | **−0.185** |
   | 48 | 442 | 18.33 % | 67.2 % | 14.5 % | +0.056 |
   | **96** | 430 | **24.19 %** | 74.0 % | 1.9 % | **+0.047** |
   | 168 | 425 | 25.65 % | 74.4 % | 0.0 % | +0.109 |
   | 336 | 403 | 26.05 % | 74.0 % | 0.0 % | +0.139 |

   At the current 24-bar horizon the meta-label is **9.35 % positive** — *more*
   degenerate than the 3-class problem it replaced, and the primary rule has
   **negative** expectancy (−0.185 ATR/trade), so there is nothing to filter. At
   96+ bars the positive rate reaches 24–26 %, sitting right at break-even with
   marginally positive expectancy. ETH agrees (26.6 % at h = 96, +0.262 ATR).
   **Meta-labelling only becomes a well-posed problem at a horizon of ~96 bars
   or more.**
3. **Threshold the probability; never use `argmax`.** With a 24 % base rate the
   argmax class is "don't trade" everywhere, which is the collapse by another
   name. The decision rule must be `P(target hit) > 1/(1 + payoff) = 0.2414`
   plus a cost margin, which is precisely what a calibrated probability is for.
   §7 shows the top feature quintile reaches 27.5 % (BTC) and 33.4 % (ETH)
   against that 24.14 % threshold — a real, thin, tradeable margin.

**What the primary signal should be.** Keep `quantum_ai_event_index()` — the
trend-pullback long filter already in `train_ml.py`. It is the live strategy's
own entry condition, so the meta-model is answering the question the book
actually asks, and geometry alignment is preserved for free. Its raw expectancy
at h = 96 is roughly flat (+0.047 ATR/trade on BTC, +0.262 on ETH), which is the
ideal starting point: a filter cannot rescue a rule with a large negative edge,
and a rule with a large positive edge does not need one.

*Code that would change:* `triple_barrier.generate_meta_labels` already
implements the correct `touch_type`-based semantics and is currently unused —
`prepare_dataset` should call it instead of re-deriving labels from
`apply_triple_barrier(...)["label"]`. `--holding` default 24 → 96. The serving
path is already built: `/meta-predict` exists in `predict_server.py` and
`get_meta_prediction` exists in `backend/services/jesse_bridge.py` with **zero
callers**; wiring the strategy's BUY gate to it is the remaining step.
Promotion gates need a new check — see below.

*Main risk:* **sample size.** There are only 1,861 QuantumAI long events in the
entire 25,248-bar BTC history, and at a 96-bar horizon they carry ~96 bars of
label overlap each, so the effective independent sample is far smaller than
1,861. At a 24 % positive rate that is roughly 450 positives total, ~90 in a
20 % holdout. This is a small-data problem and must be treated as one: pooled
cross-symbol training, heavy regularisation, uniqueness weighting that is
actually respected, and an embargo at least as long as the label span. Do not
expect a confident model; expect a weak probability estimate that needs a
threshold and position sizing to be useful.

---

## 9. Recommendation

Adopt **(d) done properly, with (b) as its precondition**: meta-label on
`touch_type == "pt"` over QuantumAI long events at 5.5/1.75 with a **96-bar**
horizon, serve a calibrated probability through the existing `/meta-predict`
endpoint, and gate entries on `P > 0.2414 + cost margin` rather than on argmax.
Do not pursue (a) — it is already enabled and already failed. Do not pursue (c)
unless (d) proves unlearnable, since it forfeits the geometry alignment the
promotion gate was built to enforce.

Three things should be fixed regardless of which remedy is chosen, because they
are bugs rather than design choices:

1. **Delete or replace `holdout_sharpe` and the DSR computed from it.** §6b
   shows a constant predictor beats the recorded model on both. Any Sharpe used
   for promotion must be computed on realised barrier returns (`tb_df["ret"]`,
   which is already available) over *taken trades only*.
2. **Add a degeneracy gate to `promotion_gates.evaluate_promotion`.** A minimum
   of `accuracy − majority_class_share > 0` and a minimum positive-class recall
   would have blocked both artifacts. Currently a model that predicts one class
   for every bar can and did record `promotion_ok: true`.
3. **Reconsider `CalibratedClassifierCV(method="isotonic", cv=3)` on a minority
   class this small.** Sigmoid/Platt calibration is far more stable at ~100
   positives per fold, and §8(a) shows isotonic is what produced the final
   collapse from an uncalibrated booster that was still emitting positives.

---

## 10. What this study did not do

- **No model was trained and no backtest, optimizer or Monte Carlo was run**, as
  instructed. Every claim about model behaviour is derived from recorded
  metadata (reconstructed exactly, §2) or from label statistics — never from a
  fitted model of mine. §7 reports raw conditional frequencies, not a fit.
- **SOL-USDT has only 960 1h bars.** Its numbers are reported for completeness
  but several cells rest on fewer than 100 events. Do not draw conclusions from
  SOL alone.
- **ETH-USDT history was being backfilled during the study** (57,600 1m bars at
  first inspection, 888,600 by extraction time). The 8,000-bar ETH window is
  valid as extracted but a later backfill will extend it.
- The headline BTC tables use the most recent 8,000 1h bars, as specified. The
  §2 reconciliation necessarily used the full 25,248-bar history because that is
  what the rejected model was trained on; both are labelled where they appear.
- Accuracy figures for the two artifacts come from their recorded metadata. I
  did not load the `.joblib` files or re-run inference, so I cannot rule out a
  metadata/artifact mismatch — though the exact agreement between the recorded
  counts and the labels recomputed from candles (§2) makes that very unlikely.
