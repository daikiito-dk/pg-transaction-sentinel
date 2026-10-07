# Banking Scenario Lab: demo guide

**English** | [日本語](#日本語)

A collection of fictional banking experiences built with Factory Desktop Droid. The application is a demonstration, not a bank, a financial product, an HR system, or production access control.

Open **Gallery** in the header to choose a demo. The chevrons select independent perspectives, not sequential workflow steps. The default URL still opens the original AML dashboard. Add `?scene=home` to open the gallery directly.

## A three-minute walkthrough

1. **Customer**: show the white internet-banking home, its clearly labeled illustrative balance, and recent synthetic transactions. Open the statement, then the transfer preview.
2. **Branch staff**: search `ACC-000002`, inspect the registered KYC category, and switch between customer inquiry, transaction inquiry, and sample requests.
3. **Sales representative → Sales manager**: create a fictional opportunity, then switch through the header to the manager view in the **same browser session**. The new opportunity contributes to the pipeline.
4. **AML analyst**: return to the dark monitoring dashboard and show an alert, its detection reasons, and the account timeline.

Use the gallery for the other examples. Refreshing or opening a new browser session may reset workflow records; they are deliberately not production records.

## Demo directory

| Scene URL | Experience | What actually runs |
| --- | --- | --- |
| `?scene=customer` | Internet-banking home, statement, service menus | PostgreSQL statement queries and aggregates. Balance and service actions are illustrative |
| `?scene=branch` | Customer search, KYC category, transaction inquiry, request list | Queries the synthetic dataset. Requests are samples |
| `?scene=aml` | Suspicious-transaction investigation | Saved Isolation Forest + rule scores from PostgreSQL |
| `?scene=onboarding` | Account-opening campaign, application, review, simulated receipt | Validation and browser-session state; no real account creation |
| `?scene=lending` | Loan cases, checklist, notes, repayment simulator | Fixed-rate amortization arithmetic and session-local case updates |
| `?scene=sales` | CRM-style manager dashboard | Pipeline aggregation with explicit, fixed stage weights |
| `?scene=opportunity` | New/edit opportunity record | Session-local records shared with the manager demo |
| `?scene=attendance` | Employee clock, history, leave requests and withdrawal | State transitions, break deduction, weekday leave counting |
| `?scene=events` | Transfer-message monitoring | Real Kafka consumption when configured; otherwise explicitly labeled samples |
| `?scene=options` | Option price, Greeks, sensitivity charts | QuantLib analytic European Black–Scholes–Merton calculations |
| `?scene=generator` | Financial test-data preview, export, local integrations | Synthetic-data generation; guarded local PostgreSQL/Keycloak writes |
| `?scene=portfolio` | Portfolio data cleaning, risk and health scoring | Pandas/NumPy analysis and scikit-learn on synthetic market data |
| `?scene=credit` | Hypothetical credit/default-risk review | LightGBM trained and evaluated on synthetic data with held-out records |

## What is deliberately a sample?

- **SENTINEL BANK** is a fictional bank. Do not enter real personal, customer, employee, or payment information.
- The customer home shows **JPY 2,450,000 as a fixed illustrative balance**, not a balance calculated from the transaction history. Net inflow/outflow is a different measure.
- Default customer/branch account: **`ACC-000002`** when present. If a smaller generated dataset lacks it, the first matching account is selected instead.
- Customer transfer fields are fixed sample values. Reviewing them does **not** create a transaction, change a balance, or make a payment.
- Application receipts, loan reviews, opportunities, clock entries, and leave requests are simulations held in the current browser session.
- CRM opportunity amounts represent business volume, not bank revenue or profit. Weighted pipeline uses demo coefficients, not a trained forecast.
- Attendance is a daily JST example. It does not calculate payroll, validate labor-law compliance, handle overnight shifts, or include Japanese public holidays.
- The loan calculator excludes fees and tax. Its checklist does not perform identity verification or query a credit bureau.
- Model outputs on synthetic data do not establish real-world AML performance, creditworthiness, investment suitability, or profitable trading.

## Models and integrations

- See [MODELS.md](MODELS.md) for the portfolio and credit algorithms and their limitations.
- See [TOOLS.md](TOOLS.md) for optional local Kafka, PostgreSQL sandbox, and Keycloak setup.
- QuantLib assumes European exercise, constant rates and volatility, and continuous dividend yield. Vega/Rho are displayed per **1 percentage point**; Theta per **calendar day**; prices per underlying unit. Market prices and investment advice are not provided.
- Read-only public demos must never enable local provisioning. The generator requires explicit local write enablement and confirmation; public mode also blocks writes server-side.
- Keycloak test-user provisioning does not mean the whole gallery has implemented authentication or production authorization.

## Presentation style

Customer screens use a friendly white/blue banking portal. Branch and employee tools use restrained white operational layouts. CRM emphasizes records and pipeline metrics. AML and Kafka monitoring use dark dashboards. Explanations sit in expandable sections so that the business interface remains the focus.

---

## 日本語

[English](#banking-scenario-lab-demo-guide) | **日本語**

Factory デスクトップ版 Droid と作った、架空の銀行業務を体験するデモ集です。実際の銀行、金融商品、人事システム、アクセス権管理を提供するものではありません。

ヘッダーの **Gallery / デモ一覧** から画面を選びます。シェブロンは独立した視点の切り替えであり、業務の進行順ではありません。通常の URL は従来の AML 画面を開き、`?scene=home` を付けると一覧を直接開けます。

### 3 分で紹介する順番

1. **顧客**：ホワイトベースの口座ホーム、サンプル残高、架空取引を表示します。明細と振込プレビューを開きます。
2. **支店担当者**：`ACC-000002` を検索し、登録済み KYC 区分・取引照会・サンプル受付一覧を見せます。
3. **営業担当者 → 営業マネージャー**：架空の商談を登録し、**同じブラウザ内でヘッダーから**管理画面に移ります。登録した商談が集計に反映されます。
4. **AML 担当者**：ダーク画面に戻り、アラート・検知理由・取引タイムラインを紹介します。

その他のデモは一覧から選べます。業務記録は本番データではないため、新しいブラウザセッションや再読み込みでリセットされる場合があります。

### デモ一覧

| URL の指定 | 体験 | 実際に動く処理 |
| --- | --- | --- |
| `?scene=customer` | 口座ホーム・明細・各種メニュー | PostgreSQL の取引照会と集計。残高と手続きはサンプル |
| `?scene=branch` | 顧客検索・KYC 区分・取引照会・受付一覧 | 架空データの照会。受付一覧はサンプル |
| `?scene=aml` | 不審取引調査 | PostgreSQL に保存された Isolation Forest とルールのスコア |
| `?scene=onboarding` | 口座開設 LP・入力・確認・デモ受付 | 入力検証とセッション保持。実口座は作成しない |
| `?scene=lending` | 融資案件・チェック・メモ・返済試算 | 固定金利の元利均等返済計算とデモ内の案件更新 |
| `?scene=sales` | CRM 風の営業管理画面 | 固定のデモ確度を使った案件集計 |
| `?scene=opportunity` | 商談の登録・更新 | 同じブラウザの管理画面と共有するデモ記録 |
| `?scene=attendance` | 打刻・勤怠一覧・休暇申請・取り下げ | 状態遷移、休憩控除、平日の休暇日数計算 |
| `?scene=events` | 振込電文モニター | 設定時は実 Kafka 接続、未設定時は明示的なサンプル |
| `?scene=options` | オプション価格・Greeks・感応度 | QuantLib の欧州型ブラック・ショールズ・マートン計算 |
| `?scene=generator` | 金融テストデータのプレビュー・出力・連携 | 合成データ生成と、制限付きのローカル DB／Keycloak 書き込み |
| `?scene=portfolio` | データクレンジング・リスク・健全性スコア | 合成価格を使った Pandas／NumPy／scikit-learn の分析 |
| `?scene=credit` | 仮想申込者の貸倒リスク確認 | 合成データの LightGBM 学習と、未学習データによる評価 |

### サンプルと実処理の区別

- **SENTINEL BANK** は架空の銀行です。実際の個人情報・顧客情報・行員情報・送金情報は入力しないでください。
- 顧客ホームの **245 万円は固定のサンプル残高**です。明細から算出した残高ではありません。入出金差額とも異なります。
- 顧客・支店画面は **`ACC-000002`** を初期選択します。データに存在しなければ最初の該当口座を使います。
- 振込画面は固定のサンプル値です。確認しても送金・残高変更・取引登録は行いません。
- 口座開設受付、融資の審査記録、商談、打刻、休暇申請は、そのブラウザセッション内だけのシミュレーションです。
- CRM 金額は取扱見込額であり、売上や利益ではありません。確度加重見込は固定係数で、学習モデルによる予測ではありません。
- 勤怠は JST の日次デモです。給与・法令適合・夜勤・日本の祝日は扱いません。
- 融資試算は手数料・税金を含まず、チェックリストは本人確認や信用情報照会を実行しません。
- 合成データ上の結果は、実際の AML 性能、信用力、投資適合性、売買利益を保証しません。

### モデルと連携の説明

- ポートフォリオと与信のモデルは [MODELS.md](MODELS.md) を参照してください。
- ローカル Kafka・PostgreSQL 専用領域・Keycloak の設定は [TOOLS.md](TOOLS.md) を参照してください。
- QuantLib は欧州型、一定金利・一定ボラ、連続配当利回りを仮定します。Vega／Rho は **1% ポイント**、Theta は **1 暦日**あたりで、価格は原資産 1 単位あたりです。市場価格や投資助言ではありません。
- 公開デモでローカルの環境作成機能を有効にしてはいけません。生成ツールの書き込みには明示的なローカル設定と確認が必要で、公開モードではサーバー側でも遮断します。
- Keycloak のテストユーザーを作成できても、デモ全体にログインや本番用の権限制御が実装されたという意味ではありません。

### 見た目

顧客画面は白・青のネットバンキング風、支店・勤怠は淡白なホワイトベースの業務画面です。CRM は商談と集計を中心に、AML・Kafka はダークな監視画面にしています。説明は折りたたみ、業務 UI を主役にしています。
