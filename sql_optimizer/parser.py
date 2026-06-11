import re
import sqlparse
from sqlparse.sql import (
    Identifier,
    IdentifierList,
    Where,
    Parenthesis,
    Function,
    Comparison,
    Operation,
)
from sqlparse.tokens import Keyword, DML, Punctuation, Wildcard, Name


class SQLParseResult:
    def __init__(self):
        self.raw_sql = ""
        self.statement_type = None
        self.tables = []
        self.columns = []
        self.where_columns = []
        self.where_functions = []
        self.join_conditions = []
        self.subqueries = []
        self.has_star = False
        self.where_clause = None
        self.order_by_columns = []
        self.group_by_columns = []
        self.limit_value = None
        self.distinct = False
        self.join_types = []
        self.table_aliases = {}


class SQLParser:
    def __init__(self):
        self.function_pattern = re.compile(
            r"\b(UPPER|LOWER|TRIM|SUBSTR|SUBSTRING|LENGTH|CONCAT|COALESCE|IFNULL|"
            r"ISNULL|CAST|CONVERT|DATE_FORMAT|YEAR|MONTH|DAY|DATE|NOW|CURDATE|"
            r"ROUND|FLOOR|CEIL|ABS|MOD|SUM|AVG|COUNT|MIN|MAX|LEFT|RIGHT|REPLACE|"
            r"REVERSE|LPAD|RPAD|INSTR|LOCATE)\s*\(",
            re.IGNORECASE,
        )

    def parse(self, sql: str) -> SQLParseResult:
        result = SQLParseResult()
        result.raw_sql = sql.strip()

        statements = sqlparse.parse(sql.strip())
        if not statements:
            return result

        stmt = statements[0]
        result.statement_type = stmt.get_type()

        if result.statement_type != "SELECT":
            return result

        self._extract_tables(stmt, result)
        self._extract_columns(stmt, result)
        self._extract_where(stmt, result)
        self._extract_subqueries(stmt, result)
        self._extract_order_group_limit(stmt, result)
        self._extract_distinct(stmt, result)
        self._extract_joins(stmt, result)

        return result

    def _extract_tables(self, stmt, result: SQLParseResult):
        from_seen = False
        for token in stmt.flatten():
            if from_seen:
                if token.ttype is Keyword and token.value.upper() in (
                    "WHERE",
                    "GROUP",
                    "ORDER",
                    "LIMIT",
                    "HAVING",
                    "JOIN",
                    "INNER",
                    "LEFT",
                    "RIGHT",
                    "FULL",
                    "CROSS",
                    "ON",
                ):
                    from_seen = False
                    continue
                if token.ttype is Punctuation:
                    continue
                if token.ttype is Keyword:
                    continue
            else:
                if token.ttype is Keyword and token.value.upper() == "FROM":
                    from_seen = True
                continue

        from_seen = False
        join_seen = False
        for token in stmt.tokens:
            if token.ttype is Keyword and token.value.upper() == "FROM":
                from_seen = True
                continue
            if from_seen and isinstance(token, IdentifierList):
                for identifier in token.get_identifiers():
                    table_name, alias = self._parse_table_identifier(identifier)
                    if table_name:
                        result.tables.append(table_name)
                        if alias:
                            result.table_aliases[alias] = table_name
                from_seen = False
            elif from_seen and isinstance(token, Identifier):
                table_name, alias = self._parse_table_identifier(token)
                if table_name:
                    result.tables.append(table_name)
                    if alias:
                        result.table_aliases[alias] = table_name
                from_seen = False
            elif token.ttype is Keyword and token.value.upper() in (
                "JOIN",
                "INNER",
                "LEFT",
                "RIGHT",
                "FULL",
                "CROSS",
            ):
                join_seen = True
            elif join_seen and isinstance(token, Identifier):
                table_name, alias = self._parse_table_identifier(token)
                if table_name:
                    result.tables.append(table_name)
                    if alias:
                        result.table_aliases[alias] = table_name
                join_seen = False

    def _parse_table_identifier(self, identifier):
        table_name = None
        alias = None
        if isinstance(identifier, Identifier):
            real_name = identifier.get_real_name()
            if real_name:
                table_name = real_name
            alias_name = identifier.get_alias()
            if alias_name:
                alias = alias_name
        return table_name, alias

    def _extract_columns(self, stmt, result: SQLParseResult):
        select_seen = False
        for token in stmt.tokens:
            if token.ttype is DML and token.value.upper() == "SELECT":
                select_seen = True
                continue
            if select_seen:
                if token.ttype is Keyword and token.value.upper() == "FROM":
                    break
                if isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        self._parse_column_identifier(identifier, result)
                elif isinstance(token, Identifier):
                    self._parse_column_identifier(token, result)
                elif token.ttype is Wildcard:
                    result.has_star = True

    def _parse_column_identifier(self, token, result: SQLParseResult):
        if isinstance(token, Identifier):
            if token.ttype is Wildcard or (
                hasattr(token, "get_name") and token.get_name() == "*"
            ):
                result.has_star = True
                return
            real_name = token.get_real_name()
            if real_name and real_name != "*":
                if token.get_parent_name():
                    result.columns.append(
                        f"{token.get_parent_name()}.{real_name}"
                    )
                else:
                    result.columns.append(real_name)
        elif hasattr(token, "ttype") and token.ttype is Wildcard:
            result.has_star = True

    def _extract_where(self, stmt, result: SQLParseResult):
        for token in stmt.tokens:
            if isinstance(token, Where):
                result.where_clause = token.value
                self._analyze_where(token, result)
                break

    def _analyze_where(self, where_token, result: SQLParseResult):
        where_text = where_token.value
        func_matches = self.function_pattern.findall(where_text)
        result.where_functions = list(set(f.upper() for f in func_matches))

        for sub_token in where_token.flatten():
            if sub_token.ttype is Name:
                result.where_columns.append(sub_token.value)

        result.where_columns = list(set(result.where_columns))

    def _extract_subqueries(self, stmt, result: SQLParseResult):
        for token in stmt.flatten():
            if isinstance(token, Parenthesis):
                inner = token.value.strip()
                if inner.startswith("(") and inner.endswith(")"):
                    inner_sql = inner[1:-1].strip()
                    if inner_sql.upper().startswith("SELECT"):
                        result.subqueries.append(inner_sql)

    def _extract_order_group_limit(self, stmt, result: SQLParseResult):
        order_seen = False
        group_seen = False
        limit_seen = False

        for token in stmt.tokens:
            if token.ttype is Keyword and token.value.upper() == "ORDER":
                order_seen = True
                continue
            if token.ttype is Keyword and token.value.upper() == "GROUP":
                group_seen = True
                continue
            if token.ttype is Keyword and token.value.upper() == "LIMIT":
                limit_seen = True
                continue
            if token.ttype is Keyword:
                if token.value.upper() not in ("BY", "ASC", "DESC"):
                    order_seen = False
                    group_seen = False
                    limit_seen = False
                continue

            if order_seen and isinstance(token, IdentifierList):
                for identifier in token.get_identifiers():
                    col = identifier.get_real_name()
                    if col:
                        result.order_by_columns.append(col)
                order_seen = False
            elif order_seen and isinstance(token, Identifier):
                col = token.get_real_name()
                if col:
                    result.order_by_columns.append(col)
                order_seen = False

            if group_seen and isinstance(token, IdentifierList):
                for identifier in token.get_identifiers():
                    col = identifier.get_real_name()
                    if col:
                        result.group_by_columns.append(col)
                group_seen = False
            elif group_seen and isinstance(token, Identifier):
                col = token.get_real_name()
                if col:
                    result.group_by_columns.append(col)
                group_seen = False

            if limit_seen:
                try:
                    result.limit_value = int(token.value)
                except (ValueError, AttributeError):
                    pass
                limit_seen = False

    def _extract_distinct(self, stmt, result: SQLParseResult):
        for token in stmt.tokens:
            if token.ttype is Keyword and token.value.upper() == "DISTINCT":
                result.distinct = True
                break

    def _extract_joins(self, stmt, result: SQLParseResult):
        tokens_upper = stmt.value.upper()
        join_keywords = ["INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"]
        for jk in join_keywords:
            count = tokens_upper.count(jk)
            for _ in range(count):
                result.join_types.append(jk)

        on_seen = False
        for token in stmt.tokens:
            if token.ttype is Keyword and token.value.upper() == "ON":
                on_seen = True
                continue
            if on_seen:
                if isinstance(token, Comparison):
                    result.join_conditions.append(token.value.strip())
                on_seen = False
