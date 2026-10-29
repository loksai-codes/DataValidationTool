"""Safe SQL identifier validation and quoting."""
from __future__ import annotations

import re

# Regex for safe unquoted alphanumeric SQL identifiers
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_identifier(name: str, allow_qualified: bool = False) -> str:
    """Validate that an identifier does not contain malicious characters.

    Raises ValueError if identifier contains disallowed characters or SQL injection tokens.
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"Invalid identifier: '{name}'. Must be a non-empty string.")

    # Check for qualification (e.g. schema.table)
    if allow_qualified:
        parts = name.split(".")
        for part in parts:
            validate_identifier(part, allow_qualified=False)
        return name

    # Reject null bytes, semicolons, comments, quotes, etc.
    if "\x00" in name or ";" in name or "--" in name or "/*" in name or "*/" in name:
        raise ValueError(f"Dangerous characters detected in identifier: {name}")

    # Remove outer quotes if already quoted
    cleaned = name.strip()
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (
        cleaned.startswith("`") and cleaned.endswith("`")
    ):
        inner = cleaned[1:-1]
        if '"' in inner or "`" in inner or ";" in inner:
            raise ValueError(f"Invalid quoted identifier: {name}")
        return cleaned

    if not SAFE_IDENTIFIER_PATTERN.match(cleaned):
        raise ValueError(
            f"Identifier '{name}' contains invalid characters. Must be alphanumeric and start with letter or underscore."
        )

    return cleaned


def quote_identifier(name: str, dialect: str = "ansi") -> str:
    """Safely quote a SQL identifier according to the dialect.

    Dialects:
    - ansi / postgres / athena / sqlite: "identifier"
    - mysql: `identifier`
    - mssql: [identifier]
    """
    if not name:
        raise ValueError("Cannot quote empty identifier.")

    # Validate first
    validated = validate_identifier(name)
    # If already safely quoted, return as is
    if (validated.startswith('"') and validated.endswith('"')) or (
        validated.startswith("`") and validated.endswith("`")
    ) or (validated.startswith("[") and validated.endswith("]")):
        return validated

    dialect_lower = dialect.lower()
    if dialect_lower == "mysql":
        escaped = validated.replace("`", "``")
        return f"`{escaped}`"
    elif dialect_lower in ("mssql", "sqlserver"):
        escaped = validated.replace("]", "]]")
        return f"[{escaped}]"
    else:  # ansi, postgres, athena, sqlite
        escaped = validated.replace('"', '""')
        return f'"{escaped}"'


def quote_qualified_table(
    catalog: str | None,
    schema: str | None,
    table: str,
    dialect: str = "ansi",
) -> str:
    """Format and quote a fully qualified table name."""
    parts = []
    if catalog:
        parts.append(quote_identifier(catalog, dialect=dialect))
    if schema:
        parts.append(quote_identifier(schema, dialect=dialect))
    parts.append(quote_identifier(table, dialect=dialect))
    return ".".join(parts)
