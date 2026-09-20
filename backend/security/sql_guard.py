"""AST-based allowlist guard for LLM-generated MySQL SELECT statements."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from sqlglot import exp, parse
from sqlglot.errors import ParseError


TABLE_SCHEMAS: Mapping[str, frozenset[str]] = MappingProxyType({
    "tickets": frozenset({
        "id", "title", "content", "category", "priority", "status",
        "service_name", "resolution", "created_at", "updated_at",
    }),
    "error_logs": frozenset({
        "id", "service_name", "level", "error_type", "message", "stack_trace",
        "request_id", "created_at",
    }),
    "knowledge_docs": frozenset({
        "id", "title", "content", "category", "source", "created_at", "updated_at",
    }),
})

ALLOWED_FUNCTIONS = frozenset({
    "AVG", "CAST", "COALESCE", "COUNT", "DATE", "DATE_FORMAT", "DAY", "HOUR",
    "LOWER", "MAX", "MIN", "MONTH", "SUM", "TIME_TO_STR", "TS_OR_DS_TO_DATE",
    "TS_OR_DS_TO_TIMESTAMP", "UPPER", "YEAR",
})

FORBIDDEN_NODE_NAMES = (
    "Alter", "Command", "Commit", "Copy", "Create", "Delete", "Drop", "Grant",
    "Hint", "Insert", "Into", "LoadData", "Lock", "Merge", "Parameter", "PropertyEQ",
    "Revoke", "Rollback", "SessionParameter", "Set", "SetOperation", "Subquery",
    "Transaction",
    "TruncateTable", "Update", "Use", "With",
)
FORBIDDEN_NODE_TYPES = tuple(
    node_type for name in FORBIDDEN_NODE_NAMES
    if (node_type := getattr(exp, name, None)) is not None
)


class SQLGuardError(ValueError):
    code = "SQL_GUARD_ERROR"


class InvalidSQLError(SQLGuardError):
    code = "INVALID_SQL"


class UnsafeSQLError(SQLGuardError):
    code = "UNSAFE_SQL"


@dataclass(frozen=True, slots=True)
class GuardedSQL:
    sql: str
    tables: tuple[str, ...]
    limit: int


class SQLGuard:
    """Parse SQL into an AST, enforce allowlists, and cap the final row limit."""

    def __init__(
        self,
        table_schemas: Mapping[str, frozenset[str]] = TABLE_SCHEMAS,
        allowed_functions: frozenset[str] = ALLOWED_FUNCTIONS,
        max_rows: int = 100,
        max_offset: int = 10_000,
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive.")
        if max_offset < 0:
            raise ValueError("max_offset must be non-negative.")
        self._table_schemas = {
            table.casefold(): frozenset(column.casefold() for column in columns)
            for table, columns in table_schemas.items()
        }
        self._allowed_functions = frozenset(name.upper() for name in allowed_functions)
        self._max_rows = max_rows
        self._max_offset = max_offset

    def validate(self, sql: str) -> GuardedSQL:
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 20_000:
            raise InvalidSQLError("SQL must be a non-empty string within 20000 characters.")
        try:
            statements = [statement for statement in parse(sql, read="mysql") if statement]
        except ParseError as exc:
            raise InvalidSQLError("SQL could not be parsed as MySQL.") from exc
        if len(statements) != 1:
            raise UnsafeSQLError("Exactly one SQL statement is allowed.")

        statement = statements[0]
        if not isinstance(statement, exp.Select):
            raise UnsafeSQLError("Only a SELECT statement is allowed.")
        self._reject_forbidden_nodes(statement)
        tables, aliases = self._validate_tables(statement)
        projection_aliases = self._validate_projections(statement)
        self._validate_columns(statement, tables, aliases, projection_aliases)
        self._validate_functions(statement)
        limit = self._apply_limit(statement)
        self._validate_offset(statement)
        return GuardedSQL(
            sql=statement.sql(dialect="mysql", comments=False),
            tables=tuple(sorted(set(tables))),
            limit=limit,
        )

    @staticmethod
    def _reject_forbidden_nodes(statement: exp.Select) -> None:
        for node in statement.walk():
            if isinstance(node, FORBIDDEN_NODE_TYPES):
                raise UnsafeSQLError(f"SQL node is not allowed: {type(node).__name__}.")

    def _validate_tables(self, statement: exp.Select) -> tuple[list[str], dict[str, str]]:
        tables: list[str] = []
        aliases: dict[str, str] = {}
        for table in statement.find_all(exp.Table):
            if table.db or table.catalog:
                raise UnsafeSQLError("Database-qualified table names are not allowed.")
            table_name = table.name.casefold()
            if table_name not in self._table_schemas:
                raise UnsafeSQLError(f"Table is not allowed: {table_name}.")
            alias = table.alias_or_name.casefold()
            if alias in aliases and aliases[alias] != table_name:
                raise UnsafeSQLError(f"Table alias is ambiguous: {alias}.")
            aliases[alias] = table_name
            aliases[table_name] = table_name
            tables.append(table_name)
        if not tables:
            raise UnsafeSQLError("A whitelisted table must be referenced.")
        return tables, aliases

    @staticmethod
    def _validate_projections(statement: exp.Select) -> frozenset[str]:
        output_names: set[str] = set()
        aliases: set[str] = set()
        for projection in statement.expressions:
            if projection.find(exp.Star) is not None:
                raise UnsafeSQLError(
                    "Wildcard * is not allowed, including COUNT(*); "
                    "use explicit columns such as COUNT(id)."
                )
            output_name = projection.alias_or_name.casefold()
            if not output_name:
                raise UnsafeSQLError("Computed columns must have an explicit alias.")
            if output_name in output_names:
                raise UnsafeSQLError(f"Duplicate output column is not allowed: {output_name}.")
            output_names.add(output_name)
            if projection.alias:
                aliases.add(projection.alias.casefold())
        return frozenset(aliases)

    def _validate_columns(
        self,
        statement: exp.Select,
        tables: list[str],
        aliases: dict[str, str],
        projection_aliases: frozenset[str],
    ) -> None:
        for column in statement.find_all(exp.Column):
            column_name = column.name.casefold()
            if column_name == "*":
                raise UnsafeSQLError("Wildcard columns are not allowed.")
            qualifier = column.table.casefold()
            if qualifier:
                table_name = aliases.get(qualifier)
                if table_name is None:
                    raise UnsafeSQLError(f"Unknown table alias: {qualifier}.")
                if column_name not in self._table_schemas[table_name]:
                    raise UnsafeSQLError(f"Column is not allowed: {column_name}.")
                continue
            if column_name in projection_aliases:
                continue
            matches = [
                table_name for table_name in tables
                if column_name in self._table_schemas[table_name]
            ]
            if not matches:
                raise UnsafeSQLError(f"Column is not allowed: {column_name}.")
            if len(matches) > 1:
                raise UnsafeSQLError(f"Unqualified column is ambiguous: {column_name}.")

    def _validate_functions(self, statement: exp.Select) -> None:
        for function in statement.find_all(exp.Func):
            # sqlglot models boolean AND/OR connectors as Func subclasses in
            # current releases, although they are operators rather than SQL
            # function calls. Their operands are validated separately.
            if isinstance(function, exp.Connector):
                continue
            name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
            normalized = name.upper()
            if normalized not in self._allowed_functions:
                raise UnsafeSQLError(f"Function is not allowed: {normalized}.")

    def _apply_limit(self, statement: exp.Select) -> int:
        limit_node = statement.args.get("limit")
        if limit_node is None:
            statement.limit(self._max_rows, copy=False)
            return self._max_rows
        value = self._nonnegative_integer(limit_node.expression, "LIMIT")
        if value > self._max_rows:
            statement.limit(self._max_rows, copy=False)
            return self._max_rows
        return value

    def _validate_offset(self, statement: exp.Select) -> None:
        offset_node = statement.args.get("offset")
        if offset_node is not None:
            value = self._nonnegative_integer(offset_node.expression, "OFFSET")
            if value > self._max_offset:
                raise UnsafeSQLError(f"OFFSET cannot exceed {self._max_offset}.")

    @staticmethod
    def _nonnegative_integer(node: exp.Expression, clause: str) -> int:
        if not isinstance(node, exp.Literal) or not node.is_int:
            raise UnsafeSQLError(f"{clause} must be a non-negative integer literal.")
        value = int(node.this)
        if value < 0:
            raise UnsafeSQLError(f"{clause} must be non-negative.")
        return value
