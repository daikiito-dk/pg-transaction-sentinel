# Model notes: portfolio health scoring and credit default risk

**English** | [日本語](#日本語)

Two independent demo scenes that run **real** algorithms on **synthetic** data. Nothing is
downloaded, nothing is written to a database, and no request leaves the browser session.

| Scene | Module | Real computation |
| --- | --- | --- |
| Investment portfolio health | `demos/portfolio.py` | pandas cleaning without look-ahead, NumPy return/covariance statistics, historical VaR, max drawdown, concentration, transparent 0-100 score, scikit-learn KMeans grouping of sampled portfolios |
| Credit default risk | `demos/credit.py` | LightGBM gradient-boosted classifier (150 shallow trees, `n_jobs=1`, deterministic), train-only standardised logistic baseline, stratified held-out evaluation, ROC AUC / precision / recall / confusion matrix, global gain importance |

Everything is reproducible from a single integer seed (`DEFAULT_SEED = 20261007`) and both
scenes render with `render()` and no arguments. Neither scene is investment advice, a credit
decision, or a production risk model.

---

## 1. Portfolio health scene (`demos/portfolio.py`)

### 1.1 Synthetic market data

Five assets over 252 business days ending 2025-12-31:

| Asset | Annual drift (synthetic) | Annual volatility (synthetic) |
| --- | --- | --- |
| Domestic Bond Fund / 国内債券 | 0.4 % | 2.5 % |
| Global Bond Fund / 海外債券 | 1.2 % | 4.5 % |
| Domestic Equity Fund / 国内株式 | 4.5 % | 16.0 % |
| Global Equity Fund / 海外株式 | 5.5 % | 20.0 % |
| REIT Fund / 不動産投信 | 4.0 % | 19.0 % |

Prices follow a correlated geometric random walk: the daily shock vector is
`z ~ N(0, I)` multiplied by the Cholesky factor of a fixed 5x5 correlation matrix
(bond-bond 0.62, equity-equity 0.72, REIT-equity 0.55-0.60, bond-equity -0.05 to 0.18).
Prices start at 100. Realised annualised volatility from one run (seed 20261007) reproduces
the configured values: 2.52 %, 4.77 %, 15.95 %, 20.42 %, 18.47 %.

The simulated feed is deliberately dirty, like a real vendor feed:

- 4 missing observations per asset in the interior of the series (21 NaN cells after
  duplicating rows that already contained gaps),
- 4 duplicated date rows,
- rows returned in shuffled order, so cleaning must sort.

### 1.2 Cleaning without look-ahead

`clean_prices(raw, ffill_limit=5)` performs, in order:

1. `sort_index(kind="stable")` — deterministic ordering even with duplicate dates,
2. `duplicated(keep="first")` — duplicate dates are dropped,
3. columns that are entirely missing are dropped (and reported); if no column survives,
   a `ValueError` is raised,
4. `ffill(limit=5)` — **past values only**, bounded; `bfill` is never used, so a future
   price can never leak into an earlier date,
5. `dropna()` — rows that cannot be completed from the past are removed, split into
   warm-up rows and interior rows in the report.

The returned report is displayed in the scene (collapsed) and is exactly the object the
tests assert on. For the default data: 256 rows received, 4 duplicates dropped, 21 missing
values filled, 0 unfilled, 252 rows clean, 0 missing values remaining.

### 1.3 Risk metrics

With weight vector `w` (validated, `Σw = 1`) and daily portfolio return
`r_t = Σ_i w_i · (P_i,t / P_i,t-1 − 1)`:

| Metric | Definition |
| --- | --- |
| Annual return | `mean(r) × 252` (arithmetic annualisation) |
| Annual volatility | `std(r, ddof=1) × sqrt(252)` |
| Historical VaR 95 / 99 | `max(−quantile(r, 0.05 or 0.01), 0) × notional`, notional ¥10,000,000 |
| Max drawdown | `min(G_t / max_{s≤t} G_s − 1)` with `G_t = Π(1 + r_s)` |
| Concentration | `HHI = Σ w_i²`, effective holdings `1 / HHI`, top weight |
| Excess return per unit risk | `(annual return − 0.5 %) / annual volatility` |

All of them are real computations on the cleaned series; no numbers are hard-coded.
Empty, single-observation, NaN, negative-notional and mismatched-weight inputs raise
`ValueError` instead of returning misleading values.

### 1.4 The 0-100 health score

Each component is scaled to 0-100 with one documented linear clip:

```
component = 100 × clip((value − worst) / (best − worst), 0, 1)
score     = Σ (component × weight)          # rounded to 1 decimal
```

| Component | `best` | `worst` | Weight |
| --- | --- | --- | --- |
| Annual volatility | 3 % | 30 % | 0.35 |
| Max drawdown (absolute) | 0 % | 35 % | 0.25 |
| Effective holdings | 5 | 1 | 0.20 |
| Excess return per unit risk | 1.0 | −0.5 | 0.20 |

Bands: `≥ 80` Resilient / 堅牢, `65–80` Balanced / 均衡, `45–65` Watch / 要注意,
`< 45` Fragile / 脆弱. The scene and this document show the same numbers, so any user can
recompute the score by hand. Example (equal weights, seed 20261007): volatility 9.99 %
→ 74.11, drawdown 11.45 % → 67.29, effective holdings 5.0 → 100.00, excess return per unit
risk 0.044 → 36.26, total **70.0 / 100 (Balanced)**.

This score is a demo construct with chosen thresholds. It is not a rating, a regulatory
measure, or a recommendation.

### 1.5 Peer grouping with KMeans

`cluster_sample_portfolios` draws 200 weight vectors from a Dirichlet(1) distribution
(uniform over the simplex), adds the user's current portfolio, and computes four metrics per
portfolio: annual volatility, annual return, |max drawdown|, effective holdings. Features are
standardised (`StandardScaler`) and grouped with `KMeans(n_clusters=3, n_init=10,
random_state=seed)`. The scene also reports the silhouette coefficient (0.336 for the default
run) and the inertia.

**Labels are derived from cluster metrics, never from cluster IDs.** Each cluster is ranked by
the mean standardised `volatility + |drawdown|` profile and then named from
`Conservative / 低リスク` to `Aggressive / 高リスク`. The default run:

| Group | Portfolios | Mean annual vol | Mean annual return | Mean \|drawdown\| | Mean effective holdings |
| --- | --- | --- | --- | --- | --- |
| Conservative / 低リスク | 101 | 8.54 % | 1.75 % | 8.69 % | 3.37 |
| Balanced / 中庸 | 41 | 13.25 % | 4.14 % | 13.07 % | 2.58 |
| Aggressive / 高リスク | 59 | 13.40 % | −3.19 % | 18.44 % | 3.03 |

A test asserts that the ordering of the labels matches the measured metrics, and that two runs
with the same seed produce identical assignments. The grouping is descriptive: it says which
allocations behaved similarly on this simulated year, not which allocation is "safe".

### 1.6 Bounds and reproducibility

- 5 assets, 252 trading days, 200 sampled portfolios, 3 clusters, 251 return observations.
- `market_data(seed)` is a cached (`st.cache_data`) immutable artifact; the scene never
  mutates it (asserted by tests).
- All randomness comes from `numpy.random.default_rng(seed)`; no global RNG state is used.
- Weights are validated before use: length, finiteness, non-negativity, non-zero sum, and
  the 100 % rule. If the entered total is not 100 %, the scene says so and normalises to
  `Σw = 1`; invalid input falls back to equal weights with an explicit message.

---

## 2. Credit default-risk scene (`demos/credit.py`)

### 2.1 Synthetic features (no protected attributes)

1,500 fictional applications, generated with `numpy.random.default_rng(20261007)`:

| Feature | Generator | Domain |
| --- | --- | --- |
| Annual income (JPY m) | log-normal(1.0, 0.5) | 1–40 |
| Debt-to-income ratio | beta(2.2, 4.0) × 0.9 | 0–0.9 |
| Credit utilisation | beta(2.0, 2.5) | 0–1 |
| Delinquencies (24 m) | Poisson(0.35) | 0–6 |
| Relationship tenure (years) | gamma(2.0, 3.0) | 0–30 |

Age, gender, nationality, ethnicity, religion, marital status and names are neither generated
nor used; a test asserts that no column name matches those terms. There is no real applicant,
account, or personal data anywhere in the scene.

### 2.2 Labels

Default propensity is a logistic function with an interaction and a debt-to-income kink:

```
logit = −4.10
        + 2.00 × utilisation
        + 0.45 × delinquencies
        + 2.00 × debt-to-income
        − 0.50 × (log10(annual income in JPY) − 6.3)
        − 0.040 × tenure
        + 2.60 × utilisation × min(delinquencies, 3)
        + 4.00 × max(debt-to-income − 0.45, 0)
        + 0.70 × noise,            noise ~ N(0, 1), seeded
default ~ Bernoulli(sigmoid(logit))
```

The noise term makes the labels reproducible but not perfectly predictable: the
Bayes-optimal ROC AUC of this simulation is **0.858**, so no honest model can approach 1.0.
The observed default rate is 16.4 % (mean propensity 16.5 %, so the labels are well
calibrated by construction).

### 2.3 Split protocol and leakage controls

- `train_test_split(test_size=0.30, stratify=y, random_state=20261007)`, index preserved:
  1,050 training rows / 450 held-out rows, no index overlap, union equal to the full data.
- Class balance is preserved: 16.4 % defaults in train (172 of 1,050) and 16.4 % in holdout
  (74 of 450).
- The logistic baseline's `StandardScaler` and `LogisticRegression` are fitted on the
  training split only; the holdout is transformed, never fitted. Tests assert the scaler's
  means equal the train means and differ from the all-data means.
- No hyperparameter is tuned on the holdout. The model configuration was chosen by
  inspecting the *training* split and the behaviour of the boosting algorithm (below), and
  it is fixed in code.
- All reported metrics are computed on the 450 held-out rows only.

### 2.4 LightGBM configuration, and why it is shallow

`LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=4, min_child_samples=30,
subsample=0.9, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, random_state=seed,
n_jobs=1, deterministic=True, force_col_wise=True)`.

With only ~1,050 training rows and 172 defaults, deep trees memorise the noisy labels. This
was measured, not assumed:

| Configuration | Train AUC | Holdout AUC |
| --- | --- | --- |
| `num_leaves=31`, `min_child_samples=20`, `reg_lambda=1.0` | 1.000 | 0.768 |
| `num_leaves=4`, `min_child_samples=30`, `reg_lambda=5.0` (shipped) | 0.869 | 0.811 |

The shipped configuration is the regularised one; the scene shows the train and holdout AUC
side by side so the absence of memorisation is visible. `n_jobs=1` keeps single-row inference
thread-safe, and `deterministic=True` with `force_col_wise=True` makes training bit-for-bit
reproducible for a given seed (asserted by tests).

### 2.5 Held-out results (measured, seed 20261007)

450 held-out records, 74 defaults (16.4 %). Threshold 0.35 is a fixed demo value.

| Metric | LightGBM | Logistic baseline (train-only scaling) |
| --- | --- | --- |
| ROC AUC | 0.811 | 0.818 |
| Precision | 0.561 | 0.667 |
| Recall | 0.500 | 0.487 |
| F1 | 0.529 | 0.563 |
| Accuracy | 0.853 | 0.876 |

Confusion matrix (rows: actual, columns: predicted at 0.35):

| Actual | Predicted low | Predicted high | Support |
| --- | --- | --- | --- |
| No default | 347 | 29 | 376 |
| Default | 37 | 37 | 74 |

The linear baseline is competitive at this sample size. That is the honest result: with 1,500
records and a near-monotone risk function, a regularised scorecard is hard to beat, and the
scene states this rather than hiding it. Both models are also far from the 0.858 simulation
ceiling, which reflects the label noise.

### 2.6 Feature importance: global, not causal

The scene plots LightGBM **gain** importance (with split counts) for the whole model:

| Feature | Gain share | Splits |
| --- | --- | --- |
| Delinquencies (24 m) | 48.7 % | 93 |
| Credit utilisation | 25.9 % | 127 |
| Debt-to-income | 14.0 % | 98 |
| Annual income | 6.8 % | 74 |
| Tenure | 4.6 % | 58 |

This ordering matches the generating formula, which is a useful sanity check. It is still only
a global, model-level statistic: it is not causal, it does not describe any individual
applicant, and no SHAP or other per-applicant explanation is produced or implied.

### 2.7 Applicant scoring and demo bands

The form scores one hypothetical applicant with a single-row `predict_proba` call
(`n_jobs=1`). It shows the probability, the percentile of that probability within the held-out
distribution, and an illustrative band:

| Band | Probability | Meaning in the demo |
| --- | --- | --- |
| A | < 10 % | Low / 低め |
| B | 10–25 % | Moderate / 中程度 |
| C | 25–50 % | Elevated / 高め |
| D | ≥ 50 % | Very high / 非常に高い |

Inputs are validated against the same domains used for generation. A manual demo review
(要審査 / 条件付き可 / 見送り) can be recorded, but it lives only in `st.session_state` for the
current browser session: nothing is written, submitted, or approved. The scene has no lending
authority and says so.

### 2.8 What these numbers do not mean

- They describe a simulation, not real underwriting. Real portfolios have different
  distributions, drift, and rejection effects.
- Probability calibration, distribution shift, and temporal drift are not evaluated.
- The 0.35 threshold and the A-D bands are illustrative constants, not credit policy.
- The cache stores an immutable training bundle (`st.cache_resource`) keyed by seed and
  record count; training happens once, not on every interaction.

---

## 3. Testing

`tests/test_portfolio.py` (17 tests) and `tests/test_credit.py` (10 tests) cover:

- reproducibility of simulated prices, splits, models and cluster labels (same seed →
  identical artifacts, different seed → different artifacts),
- cleaning correctness: duplicates removed, gaps filled from the past only, warm-up rows
  dropped, bounded `ffill` limit respected, fully missing columns dropped, empty/non-positive
  inputs rejected,
- weight and price validation guards (length, NaN, negative, zero sum, non-numeric, 100 % rule),
- metric sanity and ordering (VaR99 ≥ VaR95 > 0, equity volatility > bond volatility,
  drawdown ≤ 0, HHI and effective holdings),
- score bounds, the exact linear-clip value at a mid-band point, monotonicity in each
  component, and every band boundary,
- KMeans label derivation from cluster metrics (not cluster IDs), silhouette range, and
  identical output for repeated runs,
- credit data determinism, feature domains, absence of protected-attribute columns, noisy
  labels (train AUC < 0.99),
- stratified, disjoint, reproducible train/holdout splits and the train-only scaler (no leakage),
- held-out metric self-consistency (confusion matrix vs support vs precision/recall, threshold
  agreement, probability range),
- applicant validation, probability ordering, band assignment, and that the returned
  probability equals the model's own output,
- scene rendering with `AppTest.from_string("from demos.<module> import render\nrender()")`,
  widget interaction, weight validation messages, session isolation between two sessions,
  demo-review recording, reset behaviour, and that cached artifacts are not mutated.

Run them with `uv run pytest tests/test_portfolio.py tests/test_credit.py -q`; the full suite is
`uv run pytest -q`. Lint and format: `uv run ruff check .` and `uv run ruff format --check .`.

---

## 4. API surface (for the app shell)

`demos/portfolio.py`

- Constants: `TRADING_DAYS`, `DEFAULT_SEED`, `PRICE_START`, `PRICE_END`, `FFILL_LIMIT`,
  `DIRTY_HOLES_PER_ASSET`, `DUPLICATE_ROWS`, `SAMPLE_PORTFOLIOS`, `CLUSTERS`, `NOTIONAL_JPY`,
  `RISK_FREE_RATE`, `ASSETS`, `ASSET_SHORT`, `ASSET_DRIFT`, `ASSET_VOLATILITY`, `CORRELATION`,
  `SCORE_WEIGHTS`, `VOLATILITY_BAND`, `DRAWDOWN_BAND`, `EFFECTIVE_HOLDINGS_BAND`,
  `RISK_ADJUSTED_BAND`, `SCORE_BANDS`, `RANK_LABELS`, `CLUSTER_FEATURES`
- Functions: `render()`, `market_data(seed)`, `correlation_matrix(n)`, `simulate_prices(seed)`,
  `validate_prices(frame)`, `clean_prices(raw, ffill_limit=5)`, `daily_returns(prices)`,
  `annualized_volatility(returns)`, `annualized_return(returns)`, `historical_var(returns,
  confidence, notional)`, `max_drawdown(returns)`, `concentration(weights, assets)`,
  `portfolio_returns(returns, weights)`, `portfolio_metrics(returns, weights, assets)`,
  `health_score(metrics)`, `score_band(score)`, `validate_weights(weights)`,
  `normalize_weights(weights, expected)`, `sample_weight_vectors(n, n_assets, seed)`,
  `cluster_sample_portfolios(returns, current_weights, assets, ...)`

`demos/credit.py`

- Constants: `DEFAULT_SEED`, `N_RECORDS`, `HOLDOUT_FRACTION`, `N_ESTIMATORS`,
  `POLICY_THRESHOLD`, `FEATURES`, `TARGET`, `FEATURE_LABELS`, `FEATURE_DOMAINS`, `POLICY_BANDS`,
  `DEMO_REVIEW_OPTIONS`, `NO_PROTECTED_ATTRIBUTES`
- Functions: `render()`, `credit_model_bundle(seed, n_records)`, `build_credit_bundle(seed,
  n_records)`, `generate_credit_data(n_records, seed)`, `split_train_holdout(data, ...)`,
  `fit_lightgbm(train, seed)`, `evaluate_classifier(probabilities, labels, threshold)`,
  `feature_importance(model)`, `default_applicant()`, `validate_applicant(applicant)`,
  `policy_band(probability)`, `score_applicant(bundle, applicant)`

Integration notes:

- Both modules expose `render()` with no arguments and do not touch `app.py`,
  `banking_scenes.py`, the database, or the network.
- Session keys are scene-local: `portfolio_weight_<short>` for the five allocation inputs and
  `credit_applicant_<feature>` plus `credit_review` for the credit scene. Each scene's reset
  button clears only its own keys.
- The scenes reuse `banner()`, `demo_note()`, `table()` and `reset_button()` from
  `demos/ui.py` and therefore inherit the shared light theme classes (`branch-banner`,
  `bank-notice`, `bank-table`) injected for non-AML scenes by
  `banking_scenes.render_demo_header()`.
- Cached artifacts: `market_data` uses `st.cache_data`; `credit_model_bundle` uses
  `st.cache_resource` and returns an immutable bundle that the scene never mutates.

---

## 日本語

**日本語** | [English](#model-notes-portfolio-health-scoring-and-credit-default-risk)

2つの独立したデモ画面で、**合成データ**に対して**実際のアルゴリズム**を動かします。外部からの
ダウンロード、データベースへの書き込み、ネットワーク送信はありません。すべて
`DEFAULT_SEED = 20261007` から再現できます。どちらも投資助言や与信判断ではありません。

### 1. ポートフォリオ健全性診断（`demos/portfolio.py`）

- **データ**: 5資産・252営業日の相関付き幾何ブラウン運動（合成パラメータ）。実データ同様に
  欠損値と重複行、行順のシャッフルを混入させています。
- **クリーニング**: 日付ソート（安定ソート）→ 重複日付の削除 → 全欠損列の除外 → 上限5営業日の
  **前方補完のみ**（後方補完なし）→ 過去値で補完できない行の除外。将来の価格が過去の日付に
  入ることはありません。実施内容は画面とこの文書に同じ数値で表示されます。
- **指標**: 年率リターン（算術）`mean(r)×252`、年率ボラティリティ `std(r,ddof=1)×√252`、
  履歴VaR（95%/99%、想定元本1,000万円）、最大下落率、HHI・実効保有数、リスク調整後リターン。
- **健全性スコア（0〜100）**: 各成分を `100 × clip((値 − worst)/(best − worst), 0, 1)` で
  0〜100に変換し、重み付き合計（ボラ0.35、下落率0.25、集中度0.20、リスク調整後0.20）。
  区分は 80以上=堅牢、65〜80=均衡、45〜65=要注意、45未満=脆弱。例（均等配分、シード
  20261007）: ボラ9.99%→74.11、下落率11.45%→67.29、実効保有数5.0→100.00、
  リスク調整後0.044→36.26、合計 **70.0（均衡）**。
- **KMeansによる分類**: 単体円板上の一様分布（Dirichlet(1)）から200件の配分を抽出し、現在の配分を
  加えて4指標（ボラ・リターン・|下落率|・実効保有数）を標準化して3群に分類。群名は
  **クラスタIDではなく、実測した各群のボラティリティ＋下落率の順位**から付けています
  （Conservative→Balanced→Aggressive）。シルエット係数0.336。記述的な分類であり、
  安全性の保証や推奨ではありません。

### 2. 与信デフォルトリスク（`demos/credit.py`）

- **特徴量**: 年収（対数正規）、返済負担率（beta）、クレジット利用率（beta）、延滞件数
  （Poisson）、取引年数（gamma）。年齢・性別・国籍・人種などの保護属性は生成も使用もしていません。
- **ラベル**: 交互作用（利用率×延滞）と返済負担率のしきい値効果を含むロジスティック関数に
  シード付きノイズを加えた確率的ラベル。この模擬の理論上限AUCは0.858、実際のデフォルト率は16.4%です。
- **分割**: 層化30%ホールドアウト（学習1,050件／評価450件、行の重複なし）。比較用の
  ロジスティック回帰は標準化を含め学習データのみでfitしています（ホールドアウトは変換のみ）。
- **モデル**: LightGBM 150本、learning_rate 0.05、num_leaves 4、min_child_samples 30、
  `reg_lambda=5.0`、`n_jobs=1`、`deterministic=True`。学習1,050行では深い木（num_leaves 31）が
  ノイズを丸暗記し、学習AUC 1.000／ホールドアウトAUC 0.768まで悪化することを実測したうえで、
  浅く正則化した構成を採用しています（採用構成では学習0.869／ホールドアウト0.811）。
- **ホールドアウト性能**: ROC AUC 0.811、適合率0.561、再現率0.500、F1 0.529、正解率0.853
  （混同行列: 非デフォルト376件中29件を高リスクと誤判定、デフォルト74件中37件を検出）。
  比較用のロジスティック回帰は AUC 0.818 と同等以上で、この標本サイズでは線形スコアカードが
  競争力があるという率直な結果をそのまま表示しています。
- **重要度**: LightGBMのゲイン重要度（延滞48.7%、利用率25.9%、返済負担率14.0%、年収6.8%、
  取引年数4.6%）。生成式と整合しますが、**モデル全体の統計量であり因果関係ではありません**。
  個人ごとの説明（SHAP等）は提供していません。
- **仮想申込**: 単一行の `predict_proba` で確率を算出し、ホールドアウト分布内のパーセンタイルと
  デモ用バンド（A<10%、B 10〜25%、C 25〜50%、D≥50%）を表示。手動のデモ判定は
  `st.session_state` 内の記録のみで、送信・承認・与信判断は行いません。

### 3. テストと限界

`tests/test_portfolio.py`（17件）と `tests/test_credit.py`（10件）で、再現性、ホールドアウトの
非重複、クリーニングの将来参照防止、入力検証、スコア境界、クラスタ名の導出、学習汚染の防止、
描画（`AppTest`）とセッション分離、キャッシュ成果物の非破壊性を検証しています。
合成データでの性能は実在の審査性能を意味せず、確率較正・分布シフト・時系列ドリフトは
未検証です。しきい値とバンドは説明用の固定値であり、融資の承認・謝絶を行う権限はありません。
