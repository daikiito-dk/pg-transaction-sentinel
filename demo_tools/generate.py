"""Deterministic synthetic financial data generator with a sandboxed local write path.

The generator produces three clearly-fictional datasets for the demo:

* daily FX close rates (random walk around realistic JPY crosses),
* JPY term-deposit quote snapshots, and
* synthetic JGB-style fixed-rate bonds priced with QuantLib against a synthetic
  zero curve (clean price, yield-to-maturity, modified duration).

Everything is deterministic for a given ``seed`` and ``label``, and every record
carries a generated primary key derived from ``(seed, batch_id, business
fields)``.  Re-running the same batch produces the same keys, so inserts are
idempotent (``INSERT ... ON CONFLICT DO NOTHING``).

Writes are non-destructive by construction:

* data lands in a dedicated, new PostgreSQL schema ``demo_sandbox`` — never in
  the existing ``accounts`` / ``transactions`` / ``transaction_risk_scores``
  tables, which this module never references;
* schema and tables are created with ``IF NOT EXISTS`` only; the module never
  issues ``DROP`` or ``TRUNCATE``;
* inserts are transactional (all-or-nothing) and idempotent.

Server-side guards: all writes (database or files) are refused while
``PUBLIC_DEMO=true``, and additionally require ``LOCAL_TOOL_WRITES=true`` plus an
explicit confirmation argument. The PostgreSQL target must be a loopback host.

CLI::

    uv run python -m demo_tools.generate preview --seed 42 --days 30
    uv run python -m demo_tools.generate export --out /tmp/demo-fx.csv --dataset fx
    uv run python -m demo_tools.generate insert --seed 42 --label demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Column, Connection, DateTime, Integer, MetaData, Numeric, Table, Text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

import config
from demo_tools import is_loopback_host, mask_hostport, require_writes_enabled

SCHEMA = "demo_sandbox"
MAX_DAYS = 120
MAX_SEED = 2**31 - 1
INSERT_CHUNK = 250

FX_PAIRS = {
    "USD/JPY": (149.52, 0.0002, 0.0045),
    "EUR/JPY": (160.23, 0.0001, 0.0040),
    "GBP/JPY": (190.84, 0.0000, 0.0052),
    "AUD/JPY": (99.41, 0.0002, 0.0060),
}
DEPOSIT_TENORS = (7, 31, 92, 182, 365)
BOND_MATURITIES = (2, 5, 10, 20, 30)

# Synthetic par-yield anchors for the QuantLib zero curve (fractions, not %).
CURVE_ANCHORS = {1: 0.004, 2: 0.006, 5: 0.008, 10: 0.011, 20: 0.014, 30: 0.016}


class ToolDisabledError(PermissionError):
    """Raised when a tool write is refused by a server-side guard."""


@dataclass(frozen=True)
class GeneratorParams:
    """Clamped, validated generation parameters (everything is bounded)."""

    seed: int
    days: int
    label: str
    as_of: date
    fx_pairs: tuple[str, ...]
    deposit_tenors: tuple[int, ...]
    bond_maturities: tuple[int, ...]

    @property
    def batch_id(self) -> str:
        return f"{self.label}-{self.seed}"


def make_params(
    *,
    seed: int = 42,
    days: int = 30,
    label: str = "demo",
    as_of: date | None = None,
    fx_pairs: tuple[str, ...] | list[str] | None = None,
    deposit_tenors: tuple[int, ...] | list[int] | None = None,
    bond_maturities: tuple[int, ...] | list[int] | None = None,
) -> GeneratorParams:
    """Build bounded parameters, clamping every input to safe limits."""
    seed = int(seed) % (MAX_SEED + 1)
    days = max(1, min(int(days), MAX_DAYS))
    label = "".join(char for char in str(label).strip() if char.isalnum() or char in "-_")[:32]
    if not label:
        label = "demo"
    pairs = tuple(dict.fromkeys(p for p in (fx_pairs or ()) if p in FX_PAIRS)) or tuple(FX_PAIRS)
    tenors = tuple(sorted({int(t) for t in (deposit_tenors or ()) if int(t) in DEPOSIT_TENORS}))
    if not tenors:
        tenors = DEPOSIT_TENORS
    maturities = tuple(
        sorted({int(m) for m in (bond_maturities or ()) if int(m) in BOND_MATURITIES})
    )
    if not maturities:
        maturities = BOND_MATURITIES
    return GeneratorParams(
        seed=seed,
        days=days,
        label=label,
        as_of=as_of or date.today(),
        fx_pairs=pairs,
        deposit_tenors=tenors,
        bond_maturities=maturities,
    )


def generated_key(*parts: object) -> str:
    """Deterministic surrogate key from (seed, batch id, business fields)."""
    material = "|".join(str(part) for part in parts)
    return f"gk_{hashlib.sha1(material.encode()).hexdigest()[:20]}"


# --------------------------------------------------------------------------- #
# Dataset generation
# --------------------------------------------------------------------------- #
def _fx_rates(params: GeneratorParams, rng: np.random.Generator) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    days = [params.as_of - timedelta(days=offset) for offset in range(params.days - 1, -1, -1)]
    for pair in params.fx_pairs:
        base, quote = pair.split("/")
        start, drift, sigma = FX_PAIRS[pair]
        rate = float(start)
        for day in days:
            rate *= float(np.exp(drift + sigma * rng.standard_normal()))
            rows.append(
                {
                    "fx_key": generated_key(
                        "fx", params.seed, params.batch_id, pair, day.isoformat()
                    ),
                    "batch_id": params.batch_id,
                    "seed": params.seed,
                    "day": day,
                    "base_ccy": base,
                    "quote_ccy": quote,
                    "close_rate": round(rate, 4),
                }
            )
    return pd.DataFrame(
        rows,
        columns=["fx_key", "batch_id", "seed", "day", "base_ccy", "quote_ccy", "close_rate"],
    )


def _term_deposits(params: GeneratorParams, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for tenor in params.deposit_tenors:
        rate_pct = 0.05 + 0.40 * (tenor / 365.0) + rng.uniform(-0.05, 0.05)
        rows.append(
            {
                "deposit_key": generated_key("dep", params.seed, params.batch_id, tenor),
                "batch_id": params.batch_id,
                "seed": params.seed,
                "as_of": params.as_of,
                "tenor_days": tenor,
                "rate_pct": round(float(rate_pct), 3),
            }
        )
    return pd.DataFrame(
        rows, columns=["deposit_key", "batch_id", "seed", "as_of", "tenor_days", "rate_pct"]
    )


def _bond_quotes(params: GeneratorParams, rng: np.random.Generator) -> pd.DataFrame:
    """Price synthetic fixed-rate bonds with QuantLib against a synthetic curve."""
    import QuantLib as ql  # noqa: N813 - conventional alias; imported lazily for bonds

    eval_date = ql.Date(params.as_of.day, params.as_of.month, params.as_of.year)
    ql.Settings.instance().evaluationDate = eval_date
    day_count = ql.Actual365Fixed()

    dates = [eval_date]
    zero_rates = [0.001]  # overnight anchor
    for years, anchor in CURVE_ANCHORS.items():
        dates.append(eval_date + ql.Period(years, ql.Years))
        zero_rates.append(anchor * (1.0 + float(rng.normal(0.0, 0.02))))
    curve = ql.YieldTermStructureHandle(ql.ZeroCurve(dates, zero_rates, day_count))

    rows = []
    for years in params.bond_maturities:
        coupon = round(float(rng.uniform(0.002, 0.013)), 4)
        maturity = eval_date + ql.Period(years, ql.Years)
        schedule = ql.Schedule(
            eval_date,
            maturity,
            ql.Period(ql.Semiannual),
            ql.NullCalendar(),
            ql.Unadjusted,
            ql.Unadjusted,
            ql.DateGeneration.Backward,
            False,
        )
        bond = ql.FixedRateBond(2, 100.0, schedule, [coupon], day_count)
        bond.setPricingEngine(ql.DiscountingBondEngine(curve))
        clean_price = bond.cleanPrice()
        yield_rate = bond.bondYield(
            ql.BondPrice(clean_price, ql.BondPrice.Clean),
            day_count,
            ql.Compounded,
            ql.Annual,
        )
        modified_duration = ql.BondFunctions.duration(
            bond, yield_rate, day_count, ql.Compounded, ql.Annual, ql.Duration.Modified
        )
        rows.append(
            {
                "bond_key": generated_key("bond", params.seed, params.batch_id, years),
                "batch_id": params.batch_id,
                "seed": params.seed,
                "as_of": params.as_of,
                "bond_id": f"SYN-JGB-{years:02d}Y-{params.seed:04d}",
                "maturity_years": years,
                "maturity_date": date(maturity.year(), maturity.month(), maturity.dayOfMonth()),
                "coupon_pct": round(coupon * 100.0, 3),
                "clean_price": round(clean_price, 4),
                "yield_pct": round(yield_rate * 100.0, 4),
                "modified_duration": round(modified_duration, 4),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "bond_key",
            "batch_id",
            "seed",
            "as_of",
            "bond_id",
            "maturity_years",
            "maturity_date",
            "coupon_pct",
            "clean_price",
            "yield_pct",
            "modified_duration",
        ],
    )


@dataclass
class GeneratedBatch:
    """One generated, deterministic batch ready for preview/export/insert."""

    params: GeneratorParams
    fx_rates: pd.DataFrame
    term_deposits: pd.DataFrame
    bond_quotes: pd.DataFrame
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def batch_id(self) -> str:
        return self.params.batch_id

    @property
    def total_rows(self) -> int:
        return len(self.fx_rates) + len(self.term_deposits) + len(self.bond_quotes)

    def summary(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "seed": self.params.seed,
            "as_of": self.params.as_of.isoformat(),
            "fx_rows": len(self.fx_rates),
            "deposit_rows": len(self.term_deposits),
            "bond_rows": len(self.bond_quotes),
            "total_rows": self.total_rows,
        }


def generate_batch(params: GeneratorParams | None = None, **overrides: Any) -> GeneratedBatch:
    """Generate one deterministic synthetic batch (pure computation, no I/O)."""
    params = params or make_params(**overrides)
    rng = np.random.default_rng(params.seed)
    return GeneratedBatch(
        params=params,
        fx_rates=_fx_rates(params, rng),
        term_deposits=_term_deposits(params, rng),
        bond_quotes=_bond_quotes(params, rng),
    )


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    """UTF-8 (BOM) CSV bytes, spreadsheet-friendly and round-trippable."""
    return frame.to_csv(index=False).encode("utf-8-sig")


def to_json_bytes(frame: pd.DataFrame) -> bytes:
    """ISO-date JSON records bytes."""
    return frame.to_json(orient="records", date_format="iso").encode() + b"\n"


# --------------------------------------------------------------------------- #
# demo_sandbox schema (created, never dropped) and guarded insertion
# --------------------------------------------------------------------------- #
metadata = MetaData(schema=SCHEMA)

batches_table = Table(
    "batches",
    metadata,
    Column("batch_id", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("fx_rows", Integer, nullable=False),
    Column("deposit_rows", Integer, nullable=False),
    Column("bond_rows", Integer, nullable=False),
)

fx_table = Table(
    "fx_rates",
    metadata,
    Column("fx_key", Text, primary_key=True),
    Column("batch_id", Text, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("day", Text, nullable=False),
    Column("base_ccy", Text, nullable=False),
    Column("quote_ccy", Text, nullable=False),
    Column("close_rate", Numeric(14, 6), nullable=False),
)

deposits_table = Table(
    "term_deposits",
    metadata,
    Column("deposit_key", Text, primary_key=True),
    Column("batch_id", Text, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("as_of", Text, nullable=False),
    Column("tenor_days", Integer, nullable=False),
    Column("rate_pct", Numeric(8, 4), nullable=False),
)

bonds_table = Table(
    "bond_quotes",
    metadata,
    Column("bond_key", Text, primary_key=True),
    Column("batch_id", Text, nullable=False),
    Column("seed", Integer, nullable=False),
    Column("as_of", Text, nullable=False),
    Column("bond_id", Text, nullable=False),
    Column("maturity_years", Integer, nullable=False),
    Column("maturity_date", Text, nullable=False),
    Column("coupon_pct", Numeric(8, 4), nullable=False),
    Column("clean_price", Numeric(12, 6), nullable=False),
    Column("yield_pct", Numeric(8, 4), nullable=False),
    Column("modified_duration", Numeric(10, 4), nullable=False),
)


def ensure_sandbox(conn: Connection) -> None:
    """Create the ``demo_sandbox`` schema and its tables if they do not exist.

    This is the only schema this module ever touches, and only with
    ``CREATE ... IF NOT EXISTS``: no existing table is modified or dropped.
    """
    from sqlalchemy import text

    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    metadata.create_all(bind=conn, checkfirst=True)


def sandbox_engine() -> Engine:
    """A loopback-only PostgreSQL engine built from the app's existing config.

    Credentials are read by :func:`config.get_engine` / :func:`config.get_database_url`
    and are never echoed; only the host is inspected, and only loopback hosts are
    accepted.
    """
    from sqlalchemy.engine import make_url

    url = make_url(config.get_database_url())
    host = url.host or "localhost"
    if not is_loopback_host(host):
        raise ToolDisabledError(
            "the sandbox target must be a loopback PostgreSQL host "
            f"(got non-loopback '{host}'); refusing to write"
        )
    return config.get_engine()


def describe_target(engine: Engine) -> str:
    """Credential-free description of the sandbox destination."""
    url = engine.url
    return (
        f"PostgreSQL {mask_hostport(url.host or 'localhost', url.port or 5432)} · "
        f"database {url.database or 'aml_db'} · schema {SCHEMA}"
    )


def _insert_dataframe(conn: Connection, table: Table, frame: pd.DataFrame) -> tuple[int, int]:
    """Insert rows idempotently; returns ``(attempted, inserted)``."""
    attempted = len(frame)
    inserted = 0
    primary_key = list(table.primary_key.columns)[0].name
    records = frame.to_dict(orient="records")
    for start in range(0, len(records), INSERT_CHUNK):
        chunk = records[start : start + INSERT_CHUNK]
        if not chunk:
            continue
        statement = (
            pg_insert(table).values(chunk).on_conflict_do_nothing(index_elements=[primary_key])
        )
        inserted += conn.execute(statement).rowcount or 0
    return attempted, inserted


@dataclass(frozen=True)
class InsertResult:
    """Outcome of a guarded, transactional, idempotent sandbox insert."""

    batch_id: str
    created: bool
    attempted: int
    inserted: int
    skipped: int
    tables: dict[str, dict[str, int]]
    target: str


def insert_batch(
    batch: GeneratedBatch,
    engine: Engine,
    *,
    confirmed: bool = False,
) -> InsertResult:
    """Insert one batch into ``demo_sandbox`` under all server-side guards.

    Guards, in order: ``PUBLIC_DEMO`` must be disabled, ``LOCAL_TOOL_WRITES``
    must be enabled, the caller must pass ``confirmed=True`` (the explicit UI
    checkbox), and the engine must point at a loopback host. The insert itself is
    transactional and idempotent — re-running the same batch inserts nothing.
    """
    try:
        require_writes_enabled()
    except PermissionError as exc:
        raise ToolDisabledError(str(exc)) from exc
    if not confirmed:
        raise ToolDisabledError(
            "explicit confirmation is required before writing to the local sandbox / "
            "ローカルサンドボックスへの書き込みには明示的な確認が必要です"
        )
    host = engine.url.host or "localhost"
    if not is_loopback_host(host):
        raise ToolDisabledError(
            f"the sandbox target must be a loopback PostgreSQL host; refusing '{host}'"
        )

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        ensure_sandbox(conn)
        batch_row = {
            "batch_id": batch.batch_id,
            "label": batch.params.label,
            "seed": batch.params.seed,
            "created_at": now,
            "fx_rows": len(batch.fx_rates),
            "deposit_rows": len(batch.term_deposits),
            "bond_rows": len(batch.bond_quotes),
        }
        created = bool(
            conn.execute(
                pg_insert(batches_table)
                .values(batch_row)
                .on_conflict_do_nothing(index_elements=["batch_id"])
            ).rowcount
        )
        tables: dict[str, dict[str, int]] = {}
        attempted_total = inserted_total = 0
        for table, frame in (
            (fx_table, batch.fx_rates.assign(day=batch.fx_rates["day"].astype(str))),
            (
                deposits_table,
                batch.term_deposits.assign(as_of=batch.term_deposits["as_of"].astype(str)),
            ),
            (
                bonds_table,
                batch.bond_quotes.assign(
                    as_of=batch.bond_quotes["as_of"].astype(str),
                    maturity_date=batch.bond_quotes["maturity_date"].astype(str),
                ),
            ),
        ):
            attempted, inserted = _insert_dataframe(conn, table, frame)
            attempted_total += attempted
            inserted_total += inserted
            tables[table.name] = {"attempted": attempted, "inserted": inserted}
    return InsertResult(
        batch_id=batch.batch_id,
        created=created,
        attempted=attempted_total,
        inserted=inserted_total,
        skipped=attempted_total - inserted_total,
        tables=tables,
        target=describe_target(engine),
    )


def count_batch_rows(engine: Engine, batch_id: str) -> dict[str, int]:
    """Read-only verification of how many rows of a batch are stored."""
    from sqlalchemy import text

    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table_name in ("fx_rates", "term_deposits", "bond_quotes"):
            counts[table_name] = int(
                conn.execute(
                    text(f"SELECT COUNT(*) FROM {SCHEMA}.{table_name} WHERE batch_id = :batch_id"),
                    {"batch_id": batch_id},
                ).scalar()
            )
    return counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _params_from_args(args: argparse.Namespace) -> GeneratorParams:
    return make_params(
        seed=args.seed,
        days=args.days,
        label=args.label,
        fx_pairs=tuple(args.pairs) if getattr(args, "pairs", None) else None,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_tools.generate",
        description="Synthetic financial data generator with sandboxed local writes",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("preview", "print a JSON summary of the generated batch"),
        ("export", "write CSV/JSON files locally"),
        ("insert", "insert into the local demo_sandbox PostgreSQL schema"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--seed", type=int, default=42)
        sub.add_argument("--days", type=int, default=30)
        sub.add_argument("--label", default="demo")
        sub.add_argument("--pairs", nargs="+", choices=sorted(FX_PAIRS), default=None)

    export = subparsers.choices["export"]
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--format", choices=("csv", "json"), default="csv")
    export.add_argument("--dataset", choices=("fx", "deposits", "bonds", "all"), default="all")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    batch = generate_batch(_params_from_args(args))
    try:
        if args.command == "preview":
            print(json.dumps(batch.summary()))
            return 0
        if args.command == "export":
            require_writes_enabled()
            frames = {
                "fx": batch.fx_rates,
                "deposits": batch.term_deposits,
                "bonds": batch.bond_quotes,
            }
            datasets = list(frames) if args.dataset == "all" else [args.dataset]
            for name in datasets:
                frame = frames[name]
                suffix = args.format
                out_path = (
                    args.out
                    if args.dataset != "all"
                    else (args.out.with_name(f"{args.out.stem}-{name}.{suffix}"))
                )
                out_path.write_bytes(
                    to_csv_bytes(frame) if args.format == "csv" else to_json_bytes(frame)
                )
            print(json.dumps({"status": "ok", "written": datasets, "out": str(args.out)}))
            return 0
        result = insert_batch(batch, sandbox_engine(), confirmed=True)
        print(json.dumps(result, default=str))
        return 0
    except (ToolDisabledError, PermissionError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
