"""SQL guardrail: parse model output and accept only a single, read-only, whitelisted query.

This is one of three independent layers (the others: a read-only DB session and a query
timeout). It parses instead of pattern-matching, so tricks like comments, stacked
statements or odd casing do not slip through.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .schema_doc import ALLOWED_TABLES

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t
    for t in (
        getattr(exp, n, None)
        for n in (
            "Insert",
            "Update",
            "Delete",
            "Drop",
            "Create",
            "Alter",
            "AlterTable",
            "Command",
            "Pragma",
            "Attach",
            "Detach",
            "Set",
            "Merge",
            "Copy",
            "Transaction",
            "Commit",
            "Rollback",
            "Use",
            "TruncateTable",
            "Grant",
            "LoadData",
            "Into",
        )
    )
    if t is not None
)

FORBIDDEN_FUNCTIONS = frozenset(
    {
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "set_config",
        "current_setting",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "randomblob",
        "zeroblob",
    }
)


class UnsafeSQLError(ValueError):
    """The SQL was rejected by the guardrail. The message is safe to feed back to the model."""


def validate_sql(sql: str, *, dialect: str = "sqlite", max_rows: int = 200) -> str:
    """Return a normalised, row-limited version of ``sql`` or raise ``UnsafeSQLError``."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("empty query")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        raise UnsafeSQLError(f"syntax error: {str(exc).splitlines()[0]}") from exc
    except sqlglot.errors.TokenError as exc:
        raise UnsafeSQLError(f"syntax error: {exc}") from exc

    if len(statements) != 1:
        raise UnsafeSQLError("exactly one SQL statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise UnsafeSQLError("only SELECT queries are allowed")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQLError(f"{type(node).__name__.upper()} is not allowed")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise UnsafeSQLError(f"function {name}() is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError(f"schema-qualified table {table.sql()} is not allowed")
        if name not in ALLOWED_TABLES and name not in cte_names:
            allowed = ", ".join(sorted(ALLOWED_TABLES))
            raise UnsafeSQLError(f"unknown table '{table.name}'. Available tables: {allowed}")

    return _limit(tree, dialect, max_rows).sql(dialect=dialect)


def _limit(tree: exp.Query, dialect: str, max_rows: int) -> exp.Query:
    limit = tree.args.get("limit")
    if isinstance(tree, exp.Select):
        if limit is None:
            return tree.limit(max_rows)
        try:
            current = int(limit.expression.name)
        except (ValueError, AttributeError):
            return tree.limit(max_rows)
        return tree.limit(min(current, max_rows))
    # UNION / INTERSECT / EXCEPT: wrap so the cap applies to the combined result.
    return exp.select("*").from_(tree.subquery("combined")).limit(max_rows)
