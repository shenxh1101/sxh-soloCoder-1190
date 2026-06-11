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
    TokenList,
)
from sqlparse.tokens import Keyword, DML, Punctuation, Wildcard, Name


class SubqueryInfo:
    def __init__(self, sql, position="WHERE", subtype="correlated"):
        self.sql = sql
        self.position = position
        self.subtype = subtype


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
        self.subquery_infos = []
        self.in_subqueries = []
        self.has_star = False
        self.where_clause = None
        self.having_clause = None
        self.order_by_columns = []
        self.order_by_raw = None
        self.group_by_columns = []
        self.group_by_raw = None
        self.having_columns = []
        self.limit_value = None
        self.limit_raw = None
        self.distinct = False
        self.join_types = []
        self.table_aliases = {}
        self.aggregate_functions = []
        self.union_count = 0


class SQLParser:
    def __init__(self):
        self.function_pattern = re.compile(
            r"\b(UPPER|LOWER|TRIM|SUBSTR|SUBSTRING|LENGTH|CONCAT|COALESCE|IFNULL|"
            r"ISNULL|CAST|CONVERT|DATE_FORMAT|YEAR|MONTH|DAY|DATE|NOW|CURDATE|"
            r"ROUND|FLOOR|CEIL|ABS|MOD|SUM|AVG|COUNT|MIN|MAX|LEFT|RIGHT|REPLACE|"
            r"REVERSE|LPAD|RPAD|INSTR|LOCATE|CHAR_LENGTH|BIT_LENGTH|OCTET_LENGTH|"
            r"POSITION|FIND_IN_SET|FIELD|ELT|ASCII|ORD|BIN|HEX|OCT|CONV|FORMAT|"
            r"ROW_NUMBER|RANK|DENSE_RANK|LEAD|LAG|FIRST_VALUE|LAST_VALUE|NTH_VALUE|"
            r"STDDEV|VARIANCE|GROUP_CONCAT|JSON_EXTRACT|JSON_UNQUOTE|STRCMP|"
            r"TO_DAYS|FROM_DAYS|WEEK|QUARTER|HOUR|MINUTE|SECOND|MICROSECOND|"
            r"PERIOD_ADD|PERIOD_DIFF|TIME_TO_SEC|SEC_TO_TIME|MAKEDATE|MAKETIME)\s*\(",
            re.IGNORECASE,
        )

        self.known_functions = set(
            fn.upper() for fn in [
                "UPPER", "LOWER", "TRIM", "SUBSTR", "SUBSTRING", "LENGTH", "CONCAT",
                "COALESCE", "IFNULL", "ISNULL", "CAST", "CONVERT", "DATE_FORMAT",
                "YEAR", "MONTH", "DAY", "DATE", "NOW", "CURDATE", "ROUND", "FLOOR",
                "CEIL", "ABS", "MOD", "SUM", "AVG", "COUNT", "MIN", "MAX", "LEFT",
                "RIGHT", "REPLACE", "REVERSE", "LPAD", "RPAD", "INSTR", "LOCATE",
                "CHAR_LENGTH", "BIT_LENGTH", "OCTET_LENGTH", "POSITION",
                "FIND_IN_SET", "FIELD", "ELT", "ASCII", "ORD", "BIN", "HEX", "OCT",
                "CONV", "FORMAT", "ROW_NUMBER", "RANK", "DENSE_RANK", "LEAD", "LAG",
                "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE", "STDDEV", "VARIANCE",
                "GROUP_CONCAT", "JSON_EXTRACT", "JSON_UNQUOTE", "STRCMP",
                "TO_DAYS", "FROM_DAYS", "WEEK", "QUARTER", "HOUR", "MINUTE",
                "SECOND", "MICROSECOND", "PERIOD_ADD", "PERIOD_DIFF",
                "TIME_TO_SEC", "SEC_TO_TIME", "MAKEDATE", "MAKETIME",
            ]
        )

        self.aggregate_pattern = re.compile(
            r"\b(SUM|AVG|COUNT|MIN|MAX|GROUP_CONCAT|STDDEV|VARIANCE|BIT_AND|BIT_OR|"
            r"BIT_XOR|JSON_ARRAYAGG|JSON_OBJECTAGG)\s*\(",
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

        result.union_count = stmt.value.upper().count("UNION")

        if result.statement_type != "SELECT":
            return result

        self._extract_tables(stmt, result)
        self._extract_columns(stmt, result)
        self._extract_where_and_having(stmt, result)
        self._extract_subqueries(stmt, result)
        self._extract_in_subqueries(stmt, result)
        self._extract_order_group_limit(stmt, result)
        self._extract_distinct(stmt, result)
        self._extract_joins(stmt, result)
        self._extract_aggregates(stmt, result)

        return result

    def _extract_tables(self, stmt, result: SQLParseResult):
        from_seen = False
        join_seen = False

        for token in stmt.tokens:
            if token.is_whitespace:
                continue

            if token.ttype is Keyword and token.value.upper() == "FROM":
                from_seen = True
                continue

            upper_val = token.value.upper().strip() if token.ttype is Keyword else ""
            if upper_val in (
                "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN",
                "CROSS JOIN", "LEFT OUTER JOIN", "RIGHT OUTER JOIN",
                "FULL OUTER JOIN", "STRAIGHT_JOIN", "NATURAL JOIN",
                "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "NATURAL",
            ):
                join_seen = True
                continue

            if token.ttype is Keyword and upper_val in (
                "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "UNION", "EXCEPT", "INTERSECT",
            ):
                from_seen = False
                join_seen = False
                continue

            if from_seen or join_seen:
                if isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        table_name, alias = self._parse_table_identifier(identifier)
                        if table_name:
                            result.tables.append(table_name)
                            if alias:
                                result.table_aliases[alias] = table_name
                    from_seen = False
                    join_seen = False
                elif isinstance(token, Identifier):
                    table_name, alias = self._parse_table_identifier(token)
                    if table_name:
                        result.tables.append(table_name)
                        if alias:
                            result.table_aliases[alias] = table_name
                    if join_seen:
                        join_seen = False
                    else:
                        from_seen = False

    def _parse_table_identifier(self, identifier):
        table_name = None
        alias = None
        if isinstance(identifier, Identifier):
            real_name = identifier.get_real_name()
            if real_name and not self._is_function_call(identifier):
                table_name = real_name
            alias_name = identifier.get_alias()
            if alias_name:
                alias = alias_name
        return table_name, alias

    def _is_function_call(self, identifier):
        raw = identifier.value.strip()
        if re.match(r"^\w+\s*\(", raw, re.IGNORECASE):
            return True
        return False

    def _extract_columns(self, stmt, result: SQLParseResult):
        select_seen = False
        distinct_seen = False

        for token in stmt.tokens:
            if token.ttype is DML and token.value.upper() == "SELECT":
                select_seen = True
                continue
            if select_seen and token.ttype is Keyword and token.value.upper() == "DISTINCT":
                distinct_seen = True
                result.distinct = True
                continue
            if select_seen:
                if token.ttype is Keyword and token.value.upper() in ("FROM", "INTO"):
                    break

                if isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        self._parse_column_identifier(identifier, result)
                elif isinstance(token, Identifier):
                    self._parse_column_identifier(token, result)
                elif isinstance(token, Function):
                    self._parse_function_in_select(token, result)
                elif token.ttype is Wildcard:
                    result.has_star = True
                elif isinstance(token, Parenthesis):
                    inner = token.value.strip()
                    if inner.startswith("(") and inner.endswith(")"):
                        inner_sql = inner[1:-1].strip()
                        if inner_sql.upper().startswith("SELECT"):
                            result.subquery_infos.append(
                                SubqueryInfo(inner_sql, "SELECT_LIST", "scalar")
                            )

    def _parse_column_identifier(self, token, result: SQLParseResult):
        if isinstance(token, Identifier):
            if token.ttype is Wildcard or (
                hasattr(token, "get_name") and token.get_name() == "*"
            ):
                result.has_star = True
                return

            real_name = token.get_real_name()
            if real_name and real_name != "*":
                if self._is_function_call(token):
                    func_match = re.match(r"^(\w+)\(", token.value.strip(), re.IGNORECASE)
                    if func_match:
                        func_name = func_match.group(1).upper()
                        if func_name in self.known_functions:
                            if func_name in ("SUM", "AVG", "COUNT", "MIN", "MAX", "GROUP_CONCAT"):
                                if func_name not in result.aggregate_functions:
                                    result.aggregate_functions.append(func_name)
                    return

                if token.get_parent_name():
                    result.columns.append(
                        f"{token.get_parent_name()}.{real_name}"
                    )
                else:
                    result.columns.append(real_name)
        elif hasattr(token, "ttype") and token.ttype is Wildcard:
            result.has_star = True

    def _parse_function_in_select(self, func_token, result: SQLParseResult):
        func_name = func_token.get_name()
        if func_name:
            func_name_upper = func_name.upper()
            if func_name_upper in self.known_functions:
                if func_name_upper in ("SUM", "AVG", "COUNT", "MIN", "MAX", "GROUP_CONCAT"):
                    if func_name_upper not in result.aggregate_functions:
                        result.aggregate_functions.append(func_name_upper)

    def _extract_where_and_having(self, stmt, result: SQLParseResult):
        for token in stmt.tokens:
            if isinstance(token, Where):
                result.where_clause = token.value
                self._analyze_condition(token, result, "WHERE")
            elif isinstance(token, TokenList):
                for sub in token.tokens:
                    if sub.ttype is Keyword and sub.value.upper() == "HAVING":
                        idx = token.token_index(sub)
                        if idx + 1 < len(token.tokens):
                            having_tok = token.tokens[idx + 1]
                            result.having_clause = f"HAVING {having_tok.value.strip()}"
                            self._analyze_condition(having_tok, result, "HAVING")
                    elif sub.is_group and hasattr(sub, "tokens"):
                        for ssub in sub.tokens:
                            if ssub.ttype is Keyword and ssub.value.upper() == "HAVING":
                                hidx = sub.token_index(ssub)
                                if hidx + 1 < len(sub.tokens):
                                    having_tok = sub.tokens[hidx + 1]
                                    result.having_clause = f"HAVING {having_tok.value.strip()}"
                                    self._analyze_condition(having_tok, result, "HAVING")

        upper_sql = stmt.value.upper()
        having_match = re.search(r'\bHAVING\b(.+?)(?:ORDER BY|GROUP BY|LIMIT|$)', upper_sql, re.DOTALL | re.IGNORECASE)
        if having_match and not result.having_clause:
            result.having_clause = f"HAVING{having_match.group(1).strip()}"

    def _analyze_condition(self, condition_token, result: SQLParseResult, clause_type):
        text = condition_token.value

        func_matches = re.finditer(
            r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)',
            text,
            re.IGNORECASE,
        )
        for m in func_matches:
            func_name = m.group(1).upper()
            func_args = m.group(2)
            if func_name in self.known_functions:
                entry = {
                    "function": func_name,
                    "arguments": func_args.strip(),
                    "full_expression": m.group(0),
                }
                if entry not in result.where_functions:
                    result.where_functions.append(entry)
                if func_name in ("SUM", "AVG", "COUNT", "MIN", "MAX", "GROUP_CONCAT"):
                    if func_name not in result.aggregate_functions:
                        result.aggregate_functions.append(func_name)

        if isinstance(condition_token, TokenList):
            self._extract_columns_from_condition(condition_token, result, clause_type)

    def _extract_columns_from_condition(self, token_list, result: SQLParseResult, clause_type):
        condition_text = token_list.value

        known_tables = set(t.lower() for t in result.tables)
        known_aliases = set(k.lower() for k in result.table_aliases.keys())
        known_funcs = set(f.lower() for f in self.known_functions)

        subquery_clean = re.sub(
            r'\b(?:NOT\s+)?IN\s*\(\s*SELECT[\s\S]+?\)',
            ' ',
            condition_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        func_clean = re.sub(
            r'\b[a-zA-Z_][a-zA-Z0-9_]*\s*\([^)]*\)',
            ' ',
            subquery_clean,
            flags=re.IGNORECASE,
        )

        string_clean = re.sub(r"'[^']*'", ' ', func_clean)
        string_clean = re.sub(r'"[^"]*"', ' ', string_clean)

        num_clean = re.sub(r'\b\d+\b', ' ', string_clean)

        keyword_pattern = r'\b(AND|OR|NOT|IS|NULL|LIKE|BETWEEN|IN|EXISTS|TRUE|FALSE|UNKNOWN|AS|FROM|WHERE|GROUP|ORDER|LIMIT|HAVING|JOIN|INNER|LEFT|RIGHT|CROSS|FULL|OUTER|ON|USING|DISTINCT|SELECT|UNION|EXCEPT|INTERSECT)\b'
        keyword_clean = re.sub(keyword_pattern, ' ', num_clean, flags=re.IGNORECASE)

        op_pattern = r'[=!<>+\-*/%&|^~]'
        op_clean = re.sub(op_pattern, ' ', keyword_clean)

        identifiers = re.findall(
            r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\b',
            op_clean,
        )

        for ident in identifiers:
            ident_lower = ident.lower()

            if '.' in ident:
                parts = ident.split('.')
                prefix = parts[0].lower()
                if prefix in known_tables or prefix in known_aliases:
                    col = ident
                    if clause_type == "WHERE":
                        if col not in result.where_columns:
                            result.where_columns.append(col)
                    else:
                        if col not in result.having_columns:
                            result.having_columns.append(col)
                    continue

            if ident_lower in known_tables:
                continue
            if ident_lower in known_aliases:
                continue
            if ident_lower in known_funcs:
                continue
            if len(ident) <= 1:
                continue

            if clause_type == "WHERE":
                if ident not in result.where_columns:
                    result.where_columns.append(ident)
            else:
                if ident not in result.having_columns:
                    result.having_columns.append(ident)

    def _check_is_function(self, token):
        parent = token.parent
        if parent and isinstance(parent, Function):
            func_name = parent.get_name()
            if func_name and func_name.upper() in self.known_functions:
                return True
        return False

    def _extract_subqueries(self, stmt, result: SQLParseResult):
        def find_subqueries(tok, depth=0, position="UNKNOWN"):
            if isinstance(tok, Parenthesis):
                inner = tok.value.strip()
                if inner.startswith("(") and inner.endswith(")"):
                    inner_sql = inner[1:-1].strip()
                    if inner_sql.upper().startswith("SELECT"):
                        result.subqueries.append(inner_sql)
                        result.subquery_infos.append(
                            SubqueryInfo(inner_sql, position, "correlated")
                        )

            if hasattr(tok, "tokens"):
                for sub in tok.tokens:
                    new_pos = position
                    if hasattr(sub, "ttype") and sub.ttype is Keyword:
                        if sub.value.upper() == "WHERE":
                            new_pos = "WHERE"
                        elif sub.value.upper() == "FROM":
                            new_pos = "FROM"
                        elif sub.value.upper() == "HAVING":
                            new_pos = "HAVING"
                        elif sub.value.upper() == "SELECT":
                            new_pos = "SELECT_LIST"
                    find_subqueries(sub, depth + 1, new_pos)

        find_subqueries(stmt)

    def _extract_in_subqueries(self, stmt, result: SQLParseResult):
        sql_upper = stmt.value.upper()
        pattern = r'(\w+\.?\w*)\s+NOT\s+IN\s*\(\s*(SELECT[\s\S]+?)\)'
        matches = re.finditer(pattern, stmt.value, re.IGNORECASE | re.DOTALL)
        for m in matches:
            col = m.group(1)
            subq = m.group(2)
            result.in_subqueries.append({
                "type": "NOT_IN",
                "column": col,
                "subquery": subq.strip(),
                "original": m.group(0),
            })

        pattern = r'(\w+\.?\w*)\s+IN\s*\(\s*(SELECT[\s\S]+?)\)'
        matches = re.finditer(pattern, stmt.value, re.IGNORECASE | re.DOTALL)
        for m in matches:
            col = m.group(1)
            subq = m.group(2)
            already_notin = any(
                col == x["column"] and subq.strip() == x["subquery"].strip()
                for x in result.in_subqueries
            )
            if not already_notin:
                result.in_subqueries.append({
                    "type": "IN",
                    "column": col,
                    "subquery": subq.strip(),
                    "original": m.group(0),
                })

    def _extract_order_group_limit(self, stmt, result: SQLParseResult):
        original_sql = stmt.value

        order_full_match = re.search(
            r'\bORDER\s+BY\b\s+(.+?)(?:\bLIMIT\b|\bHAVING\b|$)',
            original_sql,
            re.DOTALL | re.IGNORECASE,
        )
        if order_full_match:
            order_full_text = order_full_match.group(1).strip()
            result.order_by_raw = order_full_text

            order_text = order_full_text.upper()
            order_items = [x.strip() for x in re.split(r',', order_full_text)]
            for item in order_items:
                clean = re.sub(r'\s+(ASC|DESC)\s*$', '', item, flags=re.IGNORECASE).strip()
                clean = re.sub(r'[()]', '', clean)
                if clean and not clean.upper().startswith("CASE"):
                    last_part = clean.split(".")[-1]
                    if last_part and not last_part.upper() in self.known_functions:
                        result.order_by_columns.append(last_part.lower())

        group_full_match = re.search(
            r'\bGROUP\s+BY\b\s+(.+?)(?:\bORDER BY\b|\bHAVING\b|\bLIMIT\b|$)',
            original_sql,
            re.DOTALL | re.IGNORECASE,
        )
        if group_full_match:
            group_full_text = group_full_match.group(1).strip()
            result.group_by_raw = group_full_text

            group_items = [x.strip() for x in re.split(r',', group_full_text)]
            for item in group_items:
                clean = re.sub(r'[()]', '', item).strip()
                if clean and not clean.upper().startswith("CASE"):
                    last_part = clean.split(".")[-1]
                    if last_part and not last_part.upper() in self.known_functions:
                        result.group_by_columns.append(last_part.lower())

        limit_full_match = re.search(
            r'\bLIMIT\s+(\d+)(?:\s*(?:,|OFFSET)\s*(\d+))?',
            original_sql,
            re.IGNORECASE,
        )
        if limit_full_match:
            limit_tokens = limit_full_match.group(0).strip()
            result.limit_raw = limit_tokens
            try:
                result.limit_value = int(limit_full_match.group(1))
            except ValueError:
                pass

    def _extract_distinct(self, stmt, result: SQLParseResult):
        for token in stmt.flatten():
            if token.ttype is Keyword and token.value.upper() == "DISTINCT":
                result.distinct = True
                break

    def _extract_joins(self, stmt, result: SQLParseResult):
        tokens_upper = stmt.value.upper()
        join_keywords = [
            "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN",
            "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN",
            "STRAIGHT_JOIN", "NATURAL JOIN",
        ]
        for jk in join_keywords:
            count = tokens_upper.count(jk)
            for _ in range(count):
                if jk not in result.join_types:
                    result.join_types.append(jk)

        on_seen = False
        current_cond = []
        for token in stmt.tokens:
            if token.is_whitespace:
                if on_seen:
                    current_cond.append(token)
                continue
            if token.ttype is Keyword and token.value.upper() == "ON":
                on_seen = True
                current_cond = []
                continue
            if on_seen:
                stop = False
                if token.ttype is Keyword and token.value.upper() in (
                    "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING",
                    "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN",
                    "FULL JOIN", "CROSS JOIN", "INNER", "LEFT", "RIGHT",
                    "CROSS", "FULL",
                ):
                    stop = True
                if isinstance(token, Where):
                    stop = True
                if stop:
                    cond = "".join([str(t) for t in current_cond]).strip()
                    cond = re.sub(r'\s+', ' ', cond)
                    if cond:
                        result.join_conditions.append(cond)
                    on_seen = False
                    current_cond = []
                    continue
                current_cond.append(token)
        if current_cond:
            cond = "".join([str(t) for t in current_cond]).strip()
            cond = re.sub(r'\s+', ' ', cond)
            if cond:
                result.join_conditions.append(cond)

    def _extract_aggregates(self, stmt, result: SQLParseResult):
        sql = stmt.value
        matches = self.aggregate_pattern.findall(sql)
        for m in matches:
            if m.upper() not in result.aggregate_functions:
                result.aggregate_functions.append(m.upper())
