# Demo Tools: Kafka Monitor, Synthetic Data Generator, Keycloak Provisioning

This document covers the optional, **local-only** companion tooling for the
demo suite. Everything here is loopback-only, off by default, and guarded so the
public deployment can never be affected.

この文書はデモスイート用の**ローカル専用**オプションツールの説明です。すべて
ループバック限定・既定で無効・ガード付きであり、公開環境には一切影響しません。

---

## Contents / 目次

1. [Security model](#security-model--セキュリティモデル)
2. [Environment variables](#environment-variables--環境変数)
3. [Transfer-message monitor (Kafka)](#1-transfer-message-monitor-kafka--振込メッセージ監視)
4. [Synthetic financial data generator](#2-synthetic-financial-data-generator--合成マーケットデータ生成)
5. [Keycloak test-user provisioning](#3-keycloak-test-user-provisioning--テストユーザー登録)
6. [docker-compose.tools.yml reference](#docker-composetoolsyml)
7. [Troubleshooting](#troubleshooting--トラブルシューティング)

---

## Security model / セキュリティモデル

| Guard / ガード | Effect / 効果 |
| --- | --- |
| `PUBLIC_DEMO=true` | Refuses **every** tool write (sandbox inserts, Keycloak provisioning, file exports, test-message producing). The demo UI also hides the write panels. / すべてのツール書き込みを拒否し、UI も書き込み欄を表示しません。 |
| `LOCAL_TOOL_WRITES=true` | Additional explicit opt-in, required *in addition* to `PUBLIC_DEMO` being disabled. / `PUBLIC_DEMO` 無効に加えて、この明示的な許可が必須です。 |
| Loopback-only destinations | Kafka brokers, PostgreSQL and Keycloak must resolve to `127.0.0.0/8`/`::1`/`localhost`. The demo UI never lets a visitor type a destination address — it only reads the environment. / 接続先はループバック限定。UI で任意のアドレスを入力できません。 |
| Explicit confirmation | UI writes require a confirmation checkbox before the insert button enables. / UI からの書き込みには確認チェックボックスが必要です。 |
| No credentials in code or logs | Passwords/tokens come from the environment or the untracked `.env.tools` only; error messages never echo them. / 認証情報は環境変数または未追跡の `.env.tools` のみ。ログ・エラーに出力しません。 |
| Non-destructive writes | Generator writes go to a new, dedicated schema `demo_sandbox` with `CREATE IF NOT EXISTS` and `INSERT ... ON CONFLICT DO NOTHING`. No `DROP`, no `TRUNCATE`, and the existing AML tables are never referenced. / 書き込みは専用スキーマ `demo_sandbox` のみ。既存テーブルには触れません。 |

Precedence: real environment variables > `.env.tools` > `.env`.

優先順位: 実環境変数 > `.env.tools` > `.env`。

---

## Environment variables / 環境変数

Copy `.env.tools.example` to `.env.tools` (git-ignored) and edit locally:

```bash
cp .env.tools.example .env.tools
python -c "import secrets; print(secrets.token_urlsafe(24))"   # -> KEYCLOAK_ADMIN_PASSWORD
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_ENABLED` | `false` | Explicit admin opt-in for the live monitor. When false, the monitor offers only sample mode and never opens a connection. / ライブ監視の明示的な有効化。 |
| `KAFKA_BOOTSTRAP_SERVERS` | `127.0.0.1:9092` | Broker list (loopback only; anything else is refused). / ブローカー（ループバックのみ）。 |
| `KAFKA_TOPIC` | `banking.transfers` | Topic the monitor consumes. / 監視対象トピック。 |
| `KEYCLOAK_URL` | `http://127.0.0.1:8080` | Local Keycloak admin API (loopback only). / ローカル Keycloak。 |
| `KEYCLOAK_ADMIN` | `demo-admin` | Bootstrap admin username. / 管理者ユーザー名。 |
| `KEYCLOAK_ADMIN_PASSWORD` | *(empty)* | Required only to run the Keycloak profile/provisioning; generate locally, never commit. / 未設定なら Keycloak は起動・登録できません。 |
| `LOCAL_TOOL_WRITES` | `false` | Explicit opt-in for all local tool writes. / ローカル書き込みの明示的な許可。 |

---

## 1. Transfer-message monitor (Kafka) / 振込メッセージ監視

Scene: `demos/events.py` — `render()` takes no arguments and is registered by
the main app. The panel auto-refreshes (Streamlit fragment, 1–5 s interval)
while monitoring is enabled.

シーン `demos/events.py` の `render()` は引数なしで本体アプリから登録されます。
監視中は 1〜5 秒間隔で自動更新します。

**Modes / モード**

* **Sample mode (default, public):** synthetic messages are generated in the
  browser session and parsed through the exact same parser. An amber
  `SAMPLE` badge is always visible; sample data is never presented as real
  Kafka traffic, and no connection is opened. / 既定のサンプルモードはブラウザ
  内生成で、常に「SAMPLE」バッジを表示し、Kafka に接続しません。
* **Live mode (admin only):** appears only when `KAFKA_ENABLED=true` *and* the
  configured broker is loopback. It consumes `banking.transfers` with a
  read-only consumer (`group_id=None`, no offset commits, offsets kept per
  browser session). Broker failures are surfaced as explicit errors and retried
  — never shown as success. / ライブモードは `KAFKA_ENABLED=true` かつ
  ループバックのときのみ表示されます。失敗は明示的にエラー表示します。

**Message format / メッセージ形式**

JSON:

```json
{"message_id":"MSG-1","account_id":"ACC-000123","counterparty_account":"EXT-001",
 "amount":950000,"currency":"JPY","transaction_type":"TRANSFER_OUT",
 "timestamp":"2026-10-07T09:15:00+09:00","channel":"mobile"}
```

XML (parsed with `defusedxml`: DTDs, entities and XXE attempts are rejected):

```xml
<transfer>
  <message_id>MSG-2</message_id><account_id>ACC-000456</account_id>
  <amount>8000</amount><currency>JPY</currency>
  <transaction_type>DEPOSIT</transaction_type>
  <timestamp>2026-10-07T00:15:00Z</timestamp><channel>branch</channel>
</transfer>
```

Malformed messages (bad JSON/XML, non-UTF-8, negative amounts, unknown types,
invalid timestamps, …) are counted, bounded previews are shown in a
malformed-message table, and the error rate feeds the KPI row and the
per-minute volume chart. / 不正なメッセージは集計され、エラー率として
チャートとKPIに表示されます。

**Start the local broker / ローカルブローカーの起動**

```bash
docker compose -f docker-compose.tools.yml --env-file .env.tools --profile kafka up -d --wait
```

The single-node KRaft broker publishes only `127.0.0.1:9092` and its JVM is
capped (`-Xmx256m`, container limit 512 MB). / 単一ノード KRaft は
127.0.0.1:9092 のみ公開し、JVM メモリを制限します。

**Trusted CLI (local verification and demo seeding) / 信頼済みCLI**

The CLI producer is for local verification by the operator. It refuses to run
when `PUBLIC_DEMO=true` or `LOCAL_TOOL_WRITES` is unset, and never accepts a
non-loopback destination. The public demo UI has **no** produce button —
visitors can never publish messages. / CLI はローカル検証用です。公開 UI には
送信ボタンがありません。

```bash
# Publish 50 synthetic messages (mixed JSON/XML, 20% deliberately malformed):
uv run python -m demo_tools.kafka_io produce --count 50 --seed 7 --format mixed --error-rate 0.2

# Run one bounded read and print a JSON summary:
uv run python -m demo_tools.kafka_io consume --max-messages 100 --timeout-ms 2000 --from earliest
```

---

## 2. Synthetic financial data generator / 合成マーケットデータ生成

Scene: `demos/generator.py` — `render()` takes no arguments (light theme).
Core module: `demo_tools/generate.py`.

シーン `demos/generator.py`（引数なし・ライトテーマ）。

**What it generates / 生成内容**

* Daily FX closes (JPY crosses) via a seeded random walk,
* JPY term-deposit quote snapshots, and
* synthetic JGB-style fixed-rate bonds priced with **QuantLib** against a
  synthetic zero curve (clean price, yield-to-maturity, modified duration).

Everything is fictional and deterministic for a given `(seed, label)`. Records
carry generated surrogate keys derived from `(kind, seed, batch_id, business
fields)`, so re-running the same batch reproduces identical rows and keys.
/ すべて架空で `(seed, label)` に対して決定的です。キーは
（種別・シード・バッチID・項目）から生成され、再実行しても同一です。

**Preview and downloads (default, safe) / プレビューとダウンロード（既定）**

The scene always shows a bounded preview first (at most 300 rows per table)
and hands the user CSV/JSON downloads generated in memory. No server-side
files are written. / 既定では範囲を限定したプレビューと、メモリ上で生成した
CSV/JSON ダウンロードのみ提供します。

CLI export writes files and therefore also requires
`PUBLIC_DEMO=false` + `LOCAL_TOOL_WRITES=true`:

```bash
uv run python -m demo_tools.generate preview --seed 42 --days 30
uv run python -m demo_tools.generate export --out /tmp/sentinel-demo.csv --dataset fx
```

**Local sandbox write / ローカルサンドボックスへの書き込み**

Guarded inserts land in a **new PostgreSQL schema `demo_sandbox`** on the
local `aml_db` (loopback only), never in the existing
`accounts`/`transactions`/`transaction_risk_scores` tables:

* the schema and its four tables (`batches`, `fx_rates`, `term_deposits`,
  `bond_quotes`) are created with `IF NOT EXISTS` only — no `DROP`, no
  `TRUNCATE`;
* inserts are transactional (all-or-nothing) and idempotent
  (`INSERT ... ON CONFLICT DO NOTHING`): re-running the same batch inserts
  nothing and reports everything as skipped;
* the UI requires all guards green plus an explicit confirmation checkbox;
* after an insert the UI shows attempted/inserted/skipped counts and a
  read-only verification of the stored rows for that batch.

書き込みは新規スキーマ `demo_sandbox` のみで、既存の AML テーブルには
一切触れません。再実行は何も挿入しません（べき等）。

```bash
uv run python -m demo_tools.generate insert --seed 42 --days 30 --label demo
```

---

## 3. Keycloak test-user provisioning / テストユーザー登録

Realm file: `deploy/keycloak/realm-demo.json` — imports the fictional realm
`sentinel-demo` with the demo roles (`aml-analyst`, `branch-staff`,
`demo-observer`) and three **credential-less** test users (fictional personas,
`.invalid` e-mail addresses). No password strings exist anywhere in the
repository. / レルムファイルには認証情報を含みません（静的パスワードなし）。

**Start local Keycloak / 起動**

```bash
docker compose -f docker-compose.tools.yml --env-file .env.tools --profile keycloak up -d --wait
```

Bound to `127.0.0.1:8080` only, JVM heap capped at 256 MB (container limit
768 MB). Requires `KEYCLOAK_ADMIN_PASSWORD` — compose refuses to start
without it. / 127.0.0.1:8080 のみで、JVM は 256MB 制限。管理者パスワードの
設定が必須です。

**Provision / 登録**

```bash
uv run python -m demo_tools.keycloak status     # realm exists? user count
uv run python -m demo_tools.keycloak provision  # ensure realm, roles, users
```

`provision` is idempotent: it ensures the realm, roles and role mappings, and
sets each test user a **randomly generated, temporary** (must-change-at-first-
login) password. The password exists in process memory only — it is never
printed, logged, or stored. The UI button in the generator scene prints only
usernames and roles. / `provision` はべき等で、一時パスワードはランダム生成
し、メモリ内のみ・非表示・未保存です。

---

## docker-compose.tools.yml

Separate compose project (`name: sentinel-tools`) so the shared `aml-postgres`
service from `docker-compose.yml` is never touched. Profiles keep both
services opt-in, ports are bound to loopback only, and JVM memory is capped:

```bash
docker compose -f docker-compose.tools.yml --env-file .env.tools --profile kafka up -d --wait
docker compose -f docker-compose.tools.yml --env-file .env.tools --profile keycloak up -d --wait
docker compose -f docker-compose.tools.yml ps
docker compose -f docker-compose.tools.yml down        # stops and removes both (volume kept)
docker compose -f docker-compose.tools.yml down -v     # also removes the kafka data volume
```

`down` removes only the `sentinel-tools` project's containers/network; the
shared `aml-postgres` container and its `aml-pgdata` volume are untouched.
/ `down` は本ツール専用のコンテナのみ削除し、共有の aml-postgres には
影響しません。

## Troubleshooting / トラブルシューティング

| Symptom / 症状 | Cause and fix / 原因と対策 |
| --- | --- |
| Live mode missing from the monitor scene / ライブモードが表示されない | `KAFKA_ENABLED` is not `true`, or `KAFKA_BOOTSTRAP_SERVERS` is not loopback. / 未設定か、ブローカーがループバックではありません。 |
| “Kafka is unavailable …” banner / Kafka 接続不可 | Broker/topic not running or topic missing. Start the kafka profile; auto-topic-creation is enabled, so produce once first. / ブローカー起動後、初回は produce が必要です。 |
| “Inserts are disabled here” / 挿入が無効 | `PUBLIC_DEMO=true` or `LOCAL_TOOL_WRITES` unset, or the local PostgreSQL is unreachable. / いずれかのガードが有効です。 |
| Compose refuses to start keycloak / Keycloak が起動しない | `KEYCLOAK_ADMIN_PASSWORD` is missing — set it in `.env.tools` and pass `--env-file .env.tools`. / `.env.tools` に設定してください。 |
| Tests skip with “local Kafka not running” / テストがスキップ | Integration tests auto-skip when the optional local services are absent; start the profiles to run them. / サービスを起動すると実行されます。 |
