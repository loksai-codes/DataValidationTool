"""
SQL dialects.

Each dialect knows how to express the handful of portable building blocks the validator
needs (identifier quoting, text casts, MD5, hex->int, epoch seconds, catalog queries).
All validation SQL is assembled from these blocks in :mod:`datavalidator.queries`, so adding
a new engine means adding one small class here.

Engine families covered:

==============  ===========================================================================
dialect         engines
==============  ===========================================================================
postgres        AWS RDS/Aurora PostgreSQL, Redshift, Alibaba RDS PostgreSQL, PolarDB-PG,
                AnalyticDB for PostgreSQL, Hologres
mysql           AWS RDS/Aurora MySQL & MariaDB, Alibaba RDS MySQL, PolarDB-MySQL (PolarDB-X),
                AnalyticDB for MySQL
athena          AWS Athena (Trino/Presto SQL) over S3 / Glue catalog
maxcompute      Alibaba Cloud MaxCompute (ODPS SQL)
duckdb          local / S3 / OSS Parquet & CSV files
sqlite          local files and the test-suite
==============  ===========================================================================
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from datavalidator.datatypes import Logical

NULL_SENTINEL = "\u0001__DV_NULL__\u0001"


def sql_literal(value: str, backslash_escapes: bool = False) -> str:
    s = str(value).replace("'", "''")
    if backslash_escapes:
        s = s.replace("\\", "\\\\")
    return f"'{s}'"


class Dialect:
    name = "ansi"
    quote_char = '"'
    backslash_escapes = False
    #: whether row-fetch queries must encode NULLs as a sentinel string (CSV-based result sets)
    uses_null_sentinel = False
    #: set for engines billed by the amount of data each query scans (shown as a cost warning)
    billing_note: Optional[str] = None

    # ---------------------------------------------------------------- identifiers & literals
    def quote(self, ident: str) -> str:
        q = self.quote_char
        return f"{q}{str(ident).replace(q, q + q)}{q}"

    def table(self, schema: Optional[str], table: str) -> str:
        if schema:
            return f"{self.quote(schema)}.{self.quote(table)}"
        return self.quote(table)

    def lit(self, value: str) -> str:
        return sql_literal(value, self.backslash_escapes)

    # ---------------------------------------------------------------- expressions
    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS VARCHAR)"

    def concat(self, parts: List[str]) -> str:
        return "(" + " || ".join(parts) + ")"

    def md5_hex(self, text_expr: str) -> str:
        """Lower-case 32 char hex MD5 of a text expression."""
        return f"md5({text_expr})"

    def substr(self, expr: str, start: int, length: int) -> str:
        return f"SUBSTR({expr}, {start}, {length})"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        """Convert an n-char (n <= 7) lower-case hex string to a non-negative integer."""
        # Portable fallback: positional arithmetic with STRPOS/INSTR.
        digits = self.lit("0123456789abcdef")
        terms = []
        for i in range(n):
            ch = self.substr(hex_expr, i + 1, 1)
            terms.append(f"({self.strpos(digits, ch)} - 1) * {16 ** (n - 1 - i)}")
        return "(" + " + ".join(terms) + ")"

    def weighted_sum(self, expr: str, logical: Logical, weight: str) -> str:
        """SUM(value * weight) without integer overflow for numeric columns."""
        return f"SUM(({expr}) * {weight})"

    def strpos(self, haystack: str, needle: str) -> str:
        return f"STRPOS({haystack}, {needle})"

    def char_length(self, expr: str) -> str:
        return f"LENGTH({expr})"

    def bool_to_int(self, expr: str) -> str:
        return f"CASE WHEN {expr} THEN 1 WHEN NOT {expr} THEN 0 END"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        raise NotImplementedError

    def numeric_sum(self, expr: str, logical: Logical) -> str:
        return f"SUM({expr})"

    def numeric_for_minmax(self, expr: str, logical: Logical) -> str:
        return expr

    def fetch_column(self, expr: str, logical: Logical) -> str:
        """Expression used when fetching a column for row-level comparison."""
        return expr

    # ---------------------------------------------------------------- statements
    def limit(self, sql: str, n: int) -> str:
        return f"{sql} LIMIT {int(n)}"

    def select_one(self) -> str:
        return "SELECT 1 AS ok"

    # ---------------------------------------------------------------- catalog
    def list_schemas_sql(self) -> str:
        return ("SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('information_schema', 'pg_catalog') ORDER BY schema_name")

    def list_tables_sql(self, schema: Optional[str]) -> str:
        where = f"WHERE table_schema = {self.lit(schema)}" if schema else ""
        return f"SELECT table_name FROM information_schema.tables {where} ORDER BY table_name"

    def primary_key_sql(self, schema: Optional[str], table: str) -> Optional[str]:
        """Query returning primary-key column names in key order (None if unsupported)."""
        return None

    def columns_sql(self, schema: Optional[str], table: str) -> str:
        conds = [f"table_name = {self.lit(table)}"]
        if schema:
            conds.append(f"table_schema = {self.lit(schema)}")
        return ("SELECT column_name, data_type, ordinal_position, is_nullable "
                f"FROM information_schema.columns WHERE {' AND '.join(conds)} ORDER BY ordinal_position")


class PostgresDialect(Dialect):
    name = "postgres"

    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS TEXT)"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        return f"(('x' || {hex_expr})::bit({4 * n})::int)"

    def weighted_sum(self, expr: str, logical: Logical, weight: str) -> str:
        if logical == Logical.FLOAT:
            return f"SUM(CAST({expr} AS DOUBLE PRECISION) * {weight})"
        return f"SUM(CAST({expr} AS NUMERIC) * {weight})"

    def char_length(self, expr: str) -> str:
        return f"CHAR_LENGTH({expr})"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        if logical == Logical.DATE:
            return f"CAST(FLOOR(EXTRACT(EPOCH FROM CAST({expr} AS TIMESTAMP))) AS BIGINT)"
        return f"CAST(FLOOR(EXTRACT(EPOCH FROM {expr})) AS BIGINT)"

    def numeric_sum(self, expr: str, logical: Logical) -> str:
        if logical == Logical.FLOAT:
            return f"SUM(CAST({expr} AS DOUBLE PRECISION))"
        return f"SUM(CAST({expr} AS NUMERIC))"

    def columns_sql(self, schema: Optional[str], table: str) -> str:
        # format_type() returns full parameterized types such as numeric(12,2), varchar(255)
        schema_cond = f"n.nspname = {self.lit(schema)}" if schema else "pg_table_is_visible(c.oid)"
        return (
            "SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type, "
            "a.attnum AS ordinal_position, CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable "
            "FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class c ON a.attrelid = c.oid "
            "JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid "
            f"WHERE {schema_cond} AND c.relname = {self.lit(table)} "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
        )

    def primary_key_sql(self, schema: Optional[str], table: str) -> Optional[str]:
        schema_cond = f"n.nspname = {self.lit(schema)}" if schema else "pg_table_is_visible(c.oid)"
        return (
            "SELECT a.attname AS column_name FROM pg_catalog.pg_index i "
            "JOIN pg_catalog.pg_class c ON c.oid = i.indrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
            f"WHERE i.indisprimary AND {schema_cond} AND c.relname = {self.lit(table)} "
            "ORDER BY array_position(i.indkey, a.attnum)"
        )

    def list_schemas_sql(self) -> str:
        return ("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN "
                "('information_schema', 'pg_catalog', 'pg_toast') AND schema_name NOT LIKE 'pg_temp%' "
                "AND schema_name NOT LIKE 'pg_toast_temp%' ORDER BY schema_name")


class RedshiftDialect(PostgresDialect):
    name = "redshift"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        # Redshift has no bit-string casts; STRTOL parses hex directly.
        return f"STRTOL({hex_expr}, 16)"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        return f"CAST(FLOOR(EXTRACT(EPOCH FROM {expr})) AS BIGINT)"


class HologresDialect(PostgresDialect):
    """Alibaba Hologres: PostgreSQL protocol, but no BIT type -> portable hex parsing."""
    name = "hologres"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        return Dialect.hex_to_int(self, hex_expr, n)


class MySQLDialect(Dialect):
    name = "mysql"
    quote_char = "`"
    backslash_escapes = True

    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS CHAR)"

    def concat(self, parts: List[str]) -> str:
        return f"CONCAT({', '.join(parts)})"

    def md5_hex(self, text_expr: str) -> str:
        return f"MD5({text_expr})"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        return f"CAST(CONV({hex_expr}, 16, 10) AS UNSIGNED)"

    def char_length(self, expr: str) -> str:
        return f"CHAR_LENGTH({expr})"

    def bool_to_int(self, expr: str) -> str:
        return f"CASE WHEN {expr} IS NULL THEN NULL WHEN {expr} THEN 1 ELSE 0 END"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        # TIMESTAMPDIFF treats the value as a wall-clock time (independent of time_zone),
        # MySQL TIMESTAMP columns are rendered in the session zone which we pin to +00:00.
        return f"TIMESTAMPDIFF(SECOND, '1970-01-01 00:00:00', {expr})"

    def list_schemas_sql(self) -> str:
        return ("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN "
                "('information_schema', 'mysql', 'performance_schema', 'sys') ORDER BY schema_name")

    def list_tables_sql(self, schema: Optional[str]) -> str:
        where = f"table_schema = {self.lit(schema)}" if schema else "table_schema = DATABASE()"
        return f"SELECT table_name FROM information_schema.tables WHERE {where} ORDER BY table_name"

    def primary_key_sql(self, schema: Optional[str], table: str) -> Optional[str]:
        where = f"table_schema = {self.lit(schema)}" if schema else "table_schema = DATABASE()"
        return ("SELECT column_name AS column_name FROM information_schema.key_column_usage "
                f"WHERE {where} AND table_name = {self.lit(table)} AND constraint_name = 'PRIMARY' "
                "ORDER BY ordinal_position")

    def columns_sql(self, schema: Optional[str], table: str) -> str:
        where = f"table_schema = {self.lit(schema)}" if schema else "table_schema = DATABASE()"
        # COLUMN_TYPE carries length / precision / unsigned, e.g. decimal(12,2), tinyint(1)
        return ("SELECT column_name AS column_name, column_type AS data_type, "
                "ordinal_position AS ordinal_position, is_nullable AS is_nullable "
                f"FROM information_schema.columns WHERE {where} AND table_name = {self.lit(table)} "
                "ORDER BY ordinal_position")


class AthenaDialect(Dialect):
    """AWS Athena (engine v3, Trino based)."""
    name = "athena"
    uses_null_sentinel = True  # results are read back as CSV from S3
    billing_note = ("Athena bills every query by the bytes it scans. Each check is at least one query, "
                    "so add a partition filter for large tables and use a workgroup with a scan limit.")

    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS VARCHAR)"

    def md5_hex(self, text_expr: str) -> str:
        return f"lower(to_hex(md5(to_utf8({text_expr}))))"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        return f"from_base({hex_expr}, 16)"

    def char_length(self, expr: str) -> str:
        return f"length({expr})"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        if logical == Logical.DATE:
            return f"CAST(FLOOR(to_unixtime(CAST({expr} AS TIMESTAMP))) AS BIGINT)"
        return f"CAST(FLOOR(to_unixtime({expr})) AS BIGINT)"

    def numeric_sum(self, expr: str, logical: Logical) -> str:
        if logical == Logical.INTEGER:
            return f"SUM(CAST({expr} AS DECIMAL(38,0)))"
        return f"SUM({expr})"

    def weighted_sum(self, expr: str, logical: Logical, weight: str) -> str:
        if logical == Logical.INTEGER:
            return f"SUM(CAST({expr} AS DECIMAL(38,0)) * {weight})"
        return f"SUM(({expr}) * {weight})"

    def fetch_column(self, expr: str, logical: Logical) -> str:
        if logical in (Logical.COMPLEX, Logical.JSON):
            inner = f"json_format(CAST({expr} AS JSON))"
        elif logical == Logical.BINARY:
            inner = f"to_hex({expr})"
        else:
            inner = f"CAST({expr} AS VARCHAR)"
        return f"COALESCE({inner}, {self.lit(NULL_SENTINEL)})"

    def list_schemas_sql(self) -> str:
        return ("SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name <> 'information_schema' ORDER BY schema_name")

    def list_tables_sql(self, schema: Optional[str]) -> str:
        where = f"WHERE table_schema = {self.lit(schema)}" if schema else ""
        return f"SELECT table_name FROM information_schema.tables {where} ORDER BY table_name"


class MaxComputeDialect(Dialect):
    """Alibaba Cloud MaxCompute (ODPS 2.0 type system)."""
    name = "maxcompute"
    quote_char = "`"
    billing_note = ("MaxCompute pay-as-you-go SQL is billed by the data each query scans, and partitioned "
                    "tables refuse full scans by default. Add a partition filter for large tables.")
    backslash_escapes = True

    def table(self, schema: Optional[str], table: str) -> str:
        # In MaxCompute the "schema" level is the project (or project.schema with 3-tier model)
        if schema:
            return ".".join(self.quote(p) for p in str(schema).split(".")) + "." + self.quote(table)
        return self.quote(table)

    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS STRING)"

    def concat(self, parts: List[str]) -> str:
        return f"CONCAT({', '.join(parts)})"

    def md5_hex(self, text_expr: str) -> str:
        return f"MD5({text_expr})"

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        return f"CAST(CONV({hex_expr}, 16, 10) AS BIGINT)"

    def strpos(self, haystack: str, needle: str) -> str:
        return f"INSTR({haystack}, {needle})"

    def char_length(self, expr: str) -> str:
        return f"LENGTH({expr})"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        return (f"DATEDIFF(CAST({expr} AS DATETIME), "
                f"CAST('1970-01-01 00:00:00' AS DATETIME), 'ss')")

    def numeric_sum(self, expr: str, logical: Logical) -> str:
        if logical == Logical.INTEGER:
            return f"SUM(CAST({expr} AS DECIMAL(38,0)))"
        return f"SUM({expr})"

    def weighted_sum(self, expr: str, logical: Logical, weight: str) -> str:
        if logical == Logical.INTEGER:
            return f"SUM(CAST({expr} AS DECIMAL(38,0)) * {weight})"
        return f"SUM(({expr}) * {weight})"

    def select_one(self) -> str:
        return "SELECT 1 AS ok"


class SQLiteDialect(Dialect):
    name = "sqlite"

    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS TEXT)"

    def md5_hex(self, text_expr: str) -> str:
        return f"md5({text_expr})"          # registered as a Python UDF by the executor

    def hex_to_int(self, hex_expr: str, n: int = 7) -> str:
        return f"hex_to_int({hex_expr})"    # registered as a Python UDF by the executor

    def strpos(self, haystack: str, needle: str) -> str:
        return f"INSTR({haystack}, {needle})"

    def bool_to_int(self, expr: str) -> str:
        return f"CASE WHEN {expr} IS NULL THEN NULL WHEN {expr} THEN 1 ELSE 0 END"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        return f"CAST(strftime('%s', {expr}) AS INTEGER)"

    def list_schemas_sql(self) -> str:
        return "SELECT 'main' AS schema_name"

    def list_tables_sql(self, schema: Optional[str]) -> str:
        return "SELECT name AS table_name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"

    def table(self, schema: Optional[str], table: str) -> str:
        if schema and schema != "main":
            return f"{self.quote(schema)}.{self.quote(table)}"
        return self.quote(table)

    def primary_key_sql(self, schema: Optional[str], table: str) -> Optional[str]:
        return f"SELECT name AS column_name FROM pragma_table_info({self.lit(table)}) WHERE pk > 0 ORDER BY pk"

    def columns_sql(self, schema: Optional[str], table: str) -> str:
        return (f"SELECT name AS column_name, type AS data_type, cid + 1 AS ordinal_position, "
                f"CASE WHEN \"notnull\" = 1 THEN 'NO' ELSE 'YES' END AS is_nullable "
                f"FROM pragma_table_info({self.lit(table)})")


class DuckDBDialect(Dialect):
    name = "duckdb"

    def cast_text(self, expr: str) -> str:
        return f"CAST({expr} AS VARCHAR)"

    def epoch_seconds(self, expr: str, logical: Logical) -> str:
        if logical == Logical.TIMESTAMP_TZ:
            return f"CAST(FLOOR(epoch({expr})) AS BIGINT)"
        return f"CAST(FLOOR(epoch(CAST({expr} AS TIMESTAMP))) AS BIGINT)"


DIALECTS: Dict[str, Type[Dialect]] = {
    "postgres": PostgresDialect,
    "redshift": RedshiftDialect,
    "hologres": HologresDialect,
    "mysql": MySQLDialect,
    "athena": AthenaDialect,
    "maxcompute": MaxComputeDialect,
    "sqlite": SQLiteDialect,
    "duckdb": DuckDBDialect,
}


def get_dialect(name: str) -> Dialect:
    try:
        return DIALECTS[name]()
    except KeyError as exc:
        raise ValueError(f"Unknown dialect '{name}'. Known: {sorted(DIALECTS)}") from exc
