# PG Transaction Sentinel

PostgreSQL × 機械学習による不審取引（AML）検知ダッシュボードの技術デモです。

日本の金融機関でも広く使われている PostgreSQL を基盤に、Isolation Forest（scikit-learn）と典型的なマネロン手口（タイポロジー）ルールを組み合わせて各取引に 0〜100 の不審度スコアを付け、Streamlit のダークモード監視画面で可視化します。AI 主導の開発（バイブコーディング）で、短時間でエンタープライズ向けプロトタイプを組み上げる例として作っています。

## アーキテクチャ

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

## クイックスタート

前提: Docker（Docker Desktop または Colima）と [uv](https://docs.astral.sh/uv/)

```bash
make setup   # 依存パッケージのインストールと .env の作成
make up      # PostgreSQL を起動（db/init/*.sql でテーブルを自動作成）
make data    # ダミー取引データを生成して投入
make score   # 不審度スコアを計算して DB に保存（検知精度も表示）
make app     # http://localhost:8501 でダッシュボードを起動
```

`make data` のオプション例: `uv run python generate_data.py --accounts 3000 --suspicious-ratio 0.05 --seed 1`

## 埋め込んでいる不審取引パターン

既定では 1,500 口座・約 37,000 件の取引を生成し、口座の 5% に次の 3 パターンのいずれかを埋め込みます。

| コード | パターン | 内容 |
| --- | --- | --- |
| `SMURFING` | スマーフィング（閾値回避） | 100 万円の閾値を避け、95〜99 万円の送金を短時間に連続して行う。原資の入金もあわせて検知 |
| `NIGHT_HIGH_VALUE_WITHDRAWAL` | 深夜の高額出金 | 普段は少額利用の口座から、午前 2〜4 時に数百万円を連続出金 |
| `DORMANT_PASS_THROUGH` | 休眠口座の即時転送 | 長期間取引のない口座に大金が入金され、数分以内に全額が別口座へ送金される |

## スコアリングの仕組み

`ml_engine.py` は取引ごとに次の特徴量を作ります。

- 金額（対数）と、口座の過去取引の中央値に対する倍率（未来の情報は使わない）
- 深夜帯フラグ（0〜5 時）
- 直近 24 時間の出金累計
- 前後 24 時間の「閾値直下送金」の件数（事後モニタリングを想定し、連続送金の最初の 1 件もさかのぼって検知）
- 休眠日数（前回取引からの経過日数）
- 入金直後の転送比率（入金から 60 分以内の出金額 ÷ 入金額）

スコアは「Isolation Forest の異常度」と「タイポロジールールの該当」を組み合わせて計算します。ルールに該当した取引には、検知理由（例: 「深夜の高額出金: 02時台に ¥4,666,030（平常時の約 689 倍）」）が付きます。

| リスクレベル | スコア | 色 |
| --- | --- | --- |
| HIGH | 80 以上 | 赤 |
| MEDIUM | 50 以上 80 未満 | 黄 |
| LOW | 50 未満 | 緑 |

既定データでの結果（score ≥ 80 を検知とみなす）は適合率 1.000、再現率 1.000 です。これは手口が明確な合成データでの数字で、実データでの性能を示すものではありません。

## ダッシュボード

- **Suspicious Transaction Alerts**: スコア順のアラート一覧。行を選ぶと口座調査パネルに切り替わります
- **Account investigation**: 口座情報、リスクバッジ、検知理由、取引タイムライン（Plotly）、取引履歴
- **Risk Overview**: スコア分布、タイポロジー別件数、日次アラート推移
- サイドバー: しきい値・タイポロジー・期間のフィルタ、再スコアリングボタン

## 開発

```bash
make test     # pytest（DB が起動していればダッシュボードの描画テストも実行）
make lint     # Ruff による lint とフォーマットチェック
make format   # 自動修正
```

## ファイル構成

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

## 注意

データはすべて架空の合成データで、実在の人物・口座とは関係ありません。本リポジトリは技術デモであり、実際の AML 業務での利用を想定したものではありません。
