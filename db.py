import io

import pandas as pd
from sqlalchemy import Connection


def copy_dataframe(conn: Connection, df: pd.DataFrame, table: str) -> None:
    """Bulk-load a DataFrame with PostgreSQL COPY (much faster than INSERT)."""
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)
    columns = ", ".join(df.columns)
    cursor = conn.connection.dbapi_connection.cursor()
    try:
        cursor.copy_expert(
            f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buffer
        )
    finally:
        cursor.close()
