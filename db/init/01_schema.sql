-- PG Transaction Sentinel schema
-- Timestamps are stored as JST wall-clock time (TIMESTAMP WITHOUT TIME ZONE)
-- so that night-time typologies (02:00-04:00) can be evaluated directly.

CREATE TABLE IF NOT EXISTS accounts (
    account_id     VARCHAR(20)  PRIMARY KEY,
    customer_name  VARCHAR(100) NOT NULL,
    created_at     TIMESTAMP    NOT NULL,
    risk_category  VARCHAR(10)  NOT NULL
        CHECK (risk_category IN ('LOW', 'MEDIUM', 'HIGH'))
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id       BIGINT        PRIMARY KEY,
    account_id           VARCHAR(20)   NOT NULL REFERENCES accounts (account_id),
    amount               NUMERIC(15,0) NOT NULL CHECK (amount > 0),
    transaction_type     VARCHAR(20)   NOT NULL
        CHECK (transaction_type IN ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER_IN', 'TRANSFER_OUT')),
    timestamp            TIMESTAMP     NOT NULL,
    destination_account  VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS idx_transactions_account_ts
    ON transactions (account_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_transactions_ts
    ON transactions (timestamp);

-- Injected money-laundering scenarios (ground truth for model evaluation only;
-- never read by the scoring engine as a feature).
CREATE TABLE IF NOT EXISTS aml_ground_truth (
    transaction_id  BIGINT      PRIMARY KEY REFERENCES transactions (transaction_id) ON DELETE CASCADE,
    typology        VARCHAR(40) NOT NULL
);

-- Output of ml_engine.py
CREATE TABLE IF NOT EXISTS transaction_risk_scores (
    transaction_id  BIGINT       PRIMARY KEY REFERENCES transactions (transaction_id) ON DELETE CASCADE,
    risk_score      NUMERIC(5,1) NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
    risk_level      VARCHAR(10)  NOT NULL CHECK (risk_level IN ('HIGH', 'MEDIUM', 'LOW')),
    ml_score        NUMERIC(5,1) NOT NULL,
    rule_hits       TEXT         NOT NULL DEFAULT '',
    reasons         TEXT         NOT NULL DEFAULT '',
    scored_at       TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_risk_scores_score
    ON transaction_risk_scores (risk_score DESC);
