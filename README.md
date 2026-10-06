# PG Transaction Sentinel

**English** | [日本語](#日本語)

A tech demo of an anti-money laundering (AML) suspicious transaction monitoring dashboard built on PostgreSQL and machine learning.

PostgreSQL stores the core banking data. An Isolation Forest model (scikit-learn) and money-laundering typology rules give every transaction a risk score from 0 to 100, and a dark-mode Streamlit dashboard shows the alerts. The project is an example of building an enterprise-style prototype in a short time with AI-driven development ("vibe coding").

## Architecture

```
[ PostgreSQL 16 (Docker) ]
   ├── accounts                 Account master
   ├── transactions             Transaction history
   ├── aml_ground_truth         Injected scenarios (evaluation only)
   └── transaction_risk_scores  Scoring results
           │
           ▼
[ ml_engine.py ]  Features → Isolation Forest + typology rules → risk score
           │
           ▼
[ app.py (Streamlit) ]  Alert queue / account investigation / risk overview
```

| Layer | Technology |
| --- | --- |
| Database | PostgreSQL 16 (Docker Compose) |
| Data access | pandas, SQLAlchemy, psycopg2 (bulk load with COPY) |
| ML | scikit-learn (Isolation Forest) |
| GUI | Streamlit, Plotly |
| Tooling | uv, Ruff, pytest |

## Quick start

Requirements: Docker (Docker Desktop or Colima) and [uv](https://docs.astral.sh/uv/)

```bash
make setup   # Install dependencies and create .env
make up      # Start PostgreSQL (tables are created from db/init/*.sql)
make data    # Generate synthetic transactions and load them
make score   # Score transactions and save results (prints detection metrics)
make app     # Start the dashboard at http://localhost:8501
```

Example options for data generation: `uv run python generate_data.py --accounts 3000 --suspicious-ratio 0.05 --seed 1`

PostgreSQL and Streamlit listen on `localhost` only.

## Injected suspicious patterns

By default the generator creates 1,500 accounts and about 37,000 transactions. It injects one of the following patterns into 5% of the accounts.

| Code | Pattern | Description |
| --- | --- | --- |
| `SMURFING` | Structuring below a threshold | Rapid transfers of JPY 950k–990k to stay under the JPY 1M reporting threshold. The funding deposit is also flagged |
| `NIGHT_HIGH_VALUE_WITHDRAWAL` | Night-time high-value withdrawal | An account that normally makes small payments suddenly withdraws millions of yen between 02:00 and 04:00 |
| `DORMANT_PASS_THROUGH` | Dormant account pass-through | A long-dormant account receives a large deposit and forwards the full amount within minutes |

## How scoring works

`ml_engine.py` builds these features for each transaction:

- Amount (log) and its ratio to the median of the account's previous transactions (no future data is used)
- Night-time flag (00:00–05:00)
- Total outflow in the last 24 hours
- Number of just-below-threshold transfers within ±24 hours (batch monitoring, so the first transfers of a burst are also caught)
- Dormancy (days since the previous transaction)
- Pass-through ratio (outflow ÷ deposit when money leaves within 60 minutes of a deposit)

The risk score combines the Isolation Forest anomaly score with typology rule hits. Each alert has a readable detection reason, for example "深夜の高額出金: 02時台に ¥4,666,030（平常時の約 689 倍）" (night-time high-value withdrawal: JPY 4,666,030 at 02:00, about 689 times the usual amount).

| Risk level | Score | Color |
| --- | --- | --- |
| HIGH | 80 or more | Red |
| MEDIUM | 50 to under 80 | Yellow |
| LOW | Under 50 | Green |

On the default data, precision and recall are both 1.000 (an alert means a score of 80 or more). These numbers come from synthetic data with clear patterns and do not show performance on real data.

## Dashboard

- **Suspicious Transaction Alerts**: Alert queue sorted by score. Selecting a row opens that account in the investigation panel
- **Account investigation**: Account profile, risk badge, detection reasons, transaction timeline (Plotly), and transaction history
- **Risk Overview**: Score distribution, alerts by typology, and daily alert volume
- Sidebar: Filters for score threshold, typology, and period, plus a re-score button

## Development

```bash
make test     # pytest (also runs a dashboard rendering test when the database is up)
make lint     # Ruff lint and format check
make format   # Auto-fix
```

## Project structure

```
├── docker-compose.yml      PostgreSQL container
├── db/init/01_schema.sql   Table definitions
├── config.py               Database connection and risk thresholds
├── db.py                   Bulk load with COPY
├── generate_data.py        Synthetic data generator
├── ml_engine.py            Features and scoring
├── app.py                  Streamlit dashboard
├── .streamlit/config.toml  Dark theme
└── tests/                  pytest
```

## Disclaimer

All data is synthetic and fictional. It is not related to any real person or account. This repository is a tech demo and is not intended for real AML operations.

## License

[MIT](LICENSE)

---

## 日本語

[English](#pg-transaction-sentinel) | **日本語**

PostgreSQL と機械学習による、不審取引（AML: アンチ・マネー・ロンダリング）検知ダッシュボードの技術デモです。

PostgreSQL に勘定系のデータを保存します。Isolation Forest（scikit-learn）と、典型的なマネロン手口（タイポロジー）のルールを組み合わせて、各取引に 0〜100 の不審度スコアを付けます。結果はダークモードの Streamlit ダッシュボードに表示します。AI 主導の開発（バイブコーディング）で、エンタープライズ向けのプロトタイプを短時間で作る例として公開しています。

### アーキテクチャ

```
[ PostgreSQL 16 (Docker) ]
   ├── accounts                 口座マスタ
   ├── transactions             取引履歴
   ├── aml_ground_truth         埋め込んだ不審シナリオ（評価専用）
   └── transaction_risk_scores  スコアリング結果
           │
           ▼
[ ml_engine.py ]  特徴量生成 → Isolation Forest + タイポロジールール → 不審度スコア
           │
           ▼
[ app.py (Streamlit) ]  アラート一覧 / 口座別調査 / リスク概況
```

| 層 | 技術 |
| --- | --- |
| DB | PostgreSQL 16（Docker Compose） |
| データ操作 | pandas, SQLAlchemy, psycopg2（COPY による一括投入） |
| ML | scikit-learn（Isolation Forest） |
| GUI | Streamlit, Plotly |
| 開発ツール | uv, Ruff, pytest |

### クイックスタート

前提: Docker（Docker Desktop または Colima）と [uv](https://docs.astral.sh/uv/)

```bash
make setup   # 依存パッケージのインストールと .env の作成
make up      # PostgreSQL を起動（db/init/*.sql でテーブルを自動作成）
make data    # ダミー取引データを生成して投入
make score   # 不審度スコアを計算して DB に保存（検知精度も表示）
make app     # http://localhost:8501 でダッシュボードを起動
```

データ生成のオプション例: `uv run python generate_data.py --accounts 3000 --suspicious-ratio 0.05 --seed 1`

PostgreSQL と Streamlit は `localhost` だけで待ち受けます。

### 埋め込んでいる不審取引パターン

既定では 1,500 口座、約 37,000 件の取引を生成し、口座の 5% に次のいずれかのパターンを埋め込みます。

| コード | パターン | 内容 |
| --- | --- | --- |
| `SMURFING` | スマーフィング（閾値回避） | 100 万円の閾値を避け、95〜99 万円の送金を短時間に連続して行う。原資の入金もあわせて検知 |
| `NIGHT_HIGH_VALUE_WITHDRAWAL` | 深夜の高額出金 | 普段は少額利用の口座から、午前 2〜4 時に数百万円を連続出金 |
| `DORMANT_PASS_THROUGH` | 休眠口座の即時転送 | 長期間取引のない口座に大金が入金され、数分以内に全額が別口座へ送金される |

### スコアリングの仕組み

`ml_engine.py` は取引ごとに次の特徴量を作ります。

- 金額（対数）と、口座の過去取引の中央値に対する倍率（未来の情報は使わない）
- 深夜帯フラグ（0〜5 時）
- 直近 24 時間の出金累計
- 前後 24 時間の「閾値直下送金」の件数（事後モニタリングを想定し、連続送金の最初の数件もさかのぼって検知）
- 休眠日数（前回取引からの経過日数）
- 入金直後の転送比率（入金から 60 分以内の出金額 ÷ 入金額）

スコアは「Isolation Forest の異常度」と「タイポロジールールの該当」を組み合わせて計算します。各アラートには、「深夜の高額出金: 02時台に ¥4,666,030（平常時の約 689 倍）」のような検知理由が付きます。

| リスクレベル | スコア | 色 |
| --- | --- | --- |
| HIGH | 80 以上 | 赤 |
| MEDIUM | 50 以上 80 未満 | 黄 |
| LOW | 50 未満 | 緑 |

既定データでの結果は、適合率・再現率ともに 1.000 です（スコア 80 以上を検知とみなした場合）。これは手口が明確な合成データでの数字で、実データでの性能を示すものではありません。

### ダッシュボード

- **Suspicious Transaction Alerts**: スコア順のアラート一覧。行を選ぶと、その口座が調査パネルに表示されます
- **Account investigation**: 口座情報、リスクバッジ、検知理由、取引タイムライン（Plotly）、取引履歴
- **Risk Overview**: スコア分布、タイポロジー別の件数、日次のアラート推移
- サイドバー: しきい値・タイポロジー・期間のフィルタと、再スコアリングボタン

### 開発

```bash
make test     # pytest（DB が起動していれば、ダッシュボードの描画テストも実行）
make lint     # Ruff による lint とフォーマットチェック
make format   # 自動修正
```

### ファイル構成

```
├── docker-compose.yml      PostgreSQL コンテナ
├── db/init/01_schema.sql   テーブル定義
├── config.py               DB 接続とリスクしきい値
├── db.py                   COPY による一括投入
├── generate_data.py        ダミーデータ生成
├── ml_engine.py            特徴量生成とスコアリング
├── app.py                  Streamlit ダッシュボード
├── .streamlit/config.toml  ダークテーマ
└── tests/                  pytest
```

### 注意

データはすべて架空の合成データで、実在の人物・口座とは関係ありません。本リポジトリは技術デモであり、実際の AML 業務での利用を想定したものではありません。

### ライセンス

[MIT](LICENSE)
