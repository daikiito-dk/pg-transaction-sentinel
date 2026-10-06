# Deploying to Amazon Lightsail

**English** | [日本語](#日本語)

This guide publishes the dashboard on a single Amazon Lightsail instance with Docker Compose. The same PostgreSQL × ML × Streamlit stack as local development runs on the server, with Caddy in front for automatic HTTPS.

```
Internet ──▶ Caddy (80/443, HTTPS) ──▶ Streamlit app ──▶ PostgreSQL
                  frontend network        backend network (internal only)
```

- Only Caddy is published. PostgreSQL and Streamlit cannot be reached from the internet.
- On first start, the `bootstrap` service generates the synthetic data and scores it. Later starts skip this step.
- `PUBLIC_DEMO=true` disables the re-score button so visitors cannot trigger heavy processing.

## Cost

| Item | Price |
| --- | --- |
| Lightsail Small-2GB Linux with public IPv4 (2 vCPU, 2 GB RAM, 60 GB SSD, 3 TB transfer) | USD 12 / month |
| Static IP (while attached to an instance) | Free |
| Domain | Your existing domain |

Prices are from the [Lightsail instance bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html) page. Check the page for current prices before you start. The stack uses about 300 MB of memory, so the 2 GB plan has headroom. The 1 GB plan is not recommended.

## 1. Prepare your AWS account

1. Sign in to the AWS console.
2. Create a budget alert under **Billing and Cost Management → Budgets**, for example USD 20 per month.

## 2. Create the instance

1. Open the [Lightsail console](https://lightsail.aws.amazon.com/) and choose **Create instance**.
2. Region: choose a region available to your account, for example **Tokyo (ap-northeast-1)** or **Sydney (ap-southeast-2)**.
3. Platform: **Linux/Unix**. Blueprint: **OS Only → Ubuntu 24.04 LTS**.
4. Plan: **Dual-stack, USD 12 (2 GB RAM)**.
5. Name the instance, for example `pg-transaction-sentinel`, and create it.

Region availability can depend on your account. During this demo's deployment, a new account was assigned Sydney, and using Tokyo required enabling additional account features. You do not need to change account features just to follow this guide; the same stack works in Sydney. Review any account-change warning before proceeding.

The [live demo](https://aml-demo.trustyon.jp) runs in **Sydney (ap-southeast-2)**. Instances and static IPs are regional resources, so choose the region before creating them.

## 3. Attach a static IP

1. Go to **Networking → Create static IP** and attach it to the instance.
2. Note the IP address.

A static IP is free while it is attached. You are charged if it is left unattached, so release it when you delete the instance.

## 4. Configure the firewall

On the instance, open **Networking → IPv4 Firewall** (and **IPv6 Firewall**):

| Application | Port | Source |
| --- | --- | --- |
| SSH | 22 | **Restrict to your own IP address** |
| HTTP | 80 | Any (needed for HTTPS certificate issuance and redirect) |
| HTTPS | 443 | Any |

Do not open port 5432 or 8501.

## 5. Add a DNS record

At your DNS provider, add one record for a subdomain:

| Type | Name | Value |
| --- | --- | --- |
| A | `aml-demo` (→ `aml-demo.trustyon.jp`) | Static IP from step 3 |

Do not change the MX records or the root domain. Email keeps working. Check that the name resolves before you continue:

```bash
dig +short aml-demo.trustyon.jp
```

## 6. Install Docker on the instance

Connect with **Connect using SSH** in the Lightsail console, then run:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu
exit
```

Reconnect so the group change takes effect, then check:

```bash
docker compose version
```

## 7. Deploy

```bash
git clone https://github.com/daikiito-dk/pg-transaction-sentinel.git
cd pg-transaction-sentinel
cp .env.production.example .env
openssl rand -hex 24   # Generates a random password. Copy the output
nano .env              # Paste it into POSTGRES_PASSWORD and set SITE_ADDRESS to your domain
docker compose -f docker-compose.prod.yml up -d --build
```

Keep `.env` on the server only. It is excluded from Git.

The first build and data setup take a few minutes. Check the status:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs bootstrap caddy
```

Open `https://aml-demo.trustyon.jp`. Caddy gets the certificate from Let's Encrypt automatically.

Without a domain, set `SITE_ADDRESS=:80` and open `http://<static IP>`. The site is then served over plain HTTP.

## Operations

| Task | Command |
| --- | --- |
| Update to the latest code | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| View logs | `docker compose -f docker-compose.prod.yml logs -f app` |
| Stop | `docker compose -f docker-compose.prod.yml down` |
| Regenerate all data (deletes the database) | `docker compose -f docker-compose.prod.yml down -v && docker compose -f docker-compose.prod.yml up -d` |

Ubuntu installs security updates automatically (unattended-upgrades is enabled by default).

## Shut down

1. Delete the instance in the Lightsail console.
2. Release the static IP under **Networking**.
3. Remove the DNS record.

---

## 日本語

[English](#deploying-to-amazon-lightsail) | **日本語**

Amazon Lightsail のインスタンス 1 台で、Docker Compose を使ってダッシュボードを公開する手順です。ローカル開発と同じ「PostgreSQL × ML × Streamlit」の構成をサーバー上で動かし、前段の Caddy が HTTPS を自動で設定します。

```
インターネット ──▶ Caddy (80/443, HTTPS) ──▶ Streamlit アプリ ──▶ PostgreSQL
                      frontend ネットワーク     backend ネットワーク（内部専用）
```

- インターネットに公開するのは Caddy だけです。PostgreSQL と Streamlit には外から接続できません。
- 初回の起動時に、`bootstrap` サービスが合成データを生成してスコアを付けます。2 回目以降の起動では、この処理は行いません。
- `PUBLIC_DEMO=true` で再スコアリングのボタンを無効にし、訪問者が重い処理を実行できないようにします。

### 費用

| 項目 | 料金 |
| --- | --- |
| Lightsail Small-2GB Linux（パブリック IPv4 付き、2 vCPU、メモリ 2GB、SSD 60GB、転送量 3TB） | 月 12 米ドル |
| 固定 IP（インスタンスに割り当て中） | 無料 |
| ドメイン | お手持ちのドメイン |

料金は [Lightsail のインスタンスプラン](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html) のページに基づいています。始める前に最新の料金を確認してください。この構成のメモリ使用量は約 300MB なので、2GB プランなら余裕があります。1GB プランはおすすめしません。

### 1. AWS アカウントの準備

1. AWS コンソールにサインインします。
2. **Billing and Cost Management → Budgets** で、予算アラートを作成します（例: 月 20 米ドル）。

### 2. インスタンスの作成

1. [Lightsail コンソール](https://lightsail.aws.amazon.com/) を開き、**インスタンスの作成** を選びます。
2. リージョン: アカウントで利用できるリージョンを選びます。例: **東京（ap-northeast-1）** または **シドニー（ap-southeast-2）**
3. プラットフォーム: **Linux/Unix**。設計図（ブループリント）: **OS のみ → Ubuntu 24.04 LTS**
4. プラン: **デュアルスタック、12 米ドル（メモリ 2GB）**
5. インスタンス名（例: `pg-transaction-sentinel`）を付けて作成します。

利用できるリージョンは、アカウントによって異なる場合があります。このデモのデプロイ時は、新規アカウントにシドニーが割り当てられ、東京の利用には追加のアカウント機能の有効化が必要でした。この手順のためだけにアカウント機能を変更する必要はありません。同じ構成をシドニーでも動かせます。アカウント変更の警告が表示された場合は、内容を確認してから進めてください。

[公開デモ](https://aml-demo.trustyon.jp)は **シドニー（ap-southeast-2）** で稼働しています。インスタンスと固定 IP はリージョンごとのリソースなので、作成前にリージョンを決めてください。

### 3. 固定 IP の割り当て

1. **ネットワーキング → 静的 IP の作成** で固定 IP を作り、インスタンスに割り当てます。
2. IP アドレスを控えておきます。

固定 IP は、インスタンスに割り当てている間は無料です。割り当てずに放置すると料金がかかるので、インスタンスを削除するときは固定 IP も解放してください。

### 4. ファイアウォールの設定

インスタンスの **ネットワーキング → IPv4 ファイアウォール**（と **IPv6 ファイアウォール**）で、次のように設定します。

| アプリケーション | ポート | 接続元 |
| --- | --- | --- |
| SSH | 22 | **自分の IP アドレスに制限する** |
| HTTP | 80 | すべて（HTTPS 証明書の発行とリダイレクトに必要） |
| HTTPS | 443 | すべて |

5432 番と 8501 番のポートは開けないでください。

### 5. DNS レコードの追加

DNS を管理しているサービスで、サブドメインのレコードを 1 件追加します。

| タイプ | 名前 | 値 |
| --- | --- | --- |
| A | `aml-demo`（→ `aml-demo.trustyon.jp`） | 手順 3 の固定 IP |

MX レコードやルートドメインの設定は変更しないでください。メールはそのまま使えます。先に進む前に、名前が引けることを確認します。

```bash
dig +short aml-demo.trustyon.jp
```

### 6. インスタンスに Docker をインストール

Lightsail コンソールの **SSH を使用して接続** でログインし、次を実行します。

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu
exit
```

グループの変更を反映するために接続し直してから、確認します。

```bash
docker compose version
```

### 7. デプロイ

```bash
git clone https://github.com/daikiito-dk/pg-transaction-sentinel.git
cd pg-transaction-sentinel
cp .env.production.example .env
openssl rand -hex 24   # ランダムなパスワードを生成。出力をコピー
nano .env              # POSTGRES_PASSWORD に貼り付け、SITE_ADDRESS をご自身のドメインに変更
docker compose -f docker-compose.prod.yml up -d --build
```

`.env` はサーバー上にだけ置いてください。Git の管理対象外です。

初回は、ビルドとデータの準備に数分かかります。状態は次のコマンドで確認できます。

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs bootstrap caddy
```

`https://aml-demo.trustyon.jp` を開きます。証明書は、Caddy が Let's Encrypt から自動で取得します。

ドメインを使わない場合は、`SITE_ADDRESS=:80` に設定して `http://<固定 IP>` を開きます。この場合は、暗号化されていない HTTP での公開になります。

### 運用

| 作業 | コマンド |
| --- | --- |
| 最新のコードに更新 | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| ログを見る | `docker compose -f docker-compose.prod.yml logs -f app` |
| 停止 | `docker compose -f docker-compose.prod.yml down` |
| データをすべて作り直す（DB を削除） | `docker compose -f docker-compose.prod.yml down -v && docker compose -f docker-compose.prod.yml up -d` |

Ubuntu では、セキュリティ更新が自動でインストールされます（unattended-upgrades が既定で有効）。

### 公開の終了

1. Lightsail コンソールでインスタンスを削除します。
2. **ネットワーキング** で固定 IP を解放します。
3. DNS レコードを削除します。
