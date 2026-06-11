import re
from .parser import SQLParser, SQLParseResult


class OptimizationSuggestion:
    def __init__(self, rule_id, severity, title, description, original=None, suggested=None, details=None):
        self.rule_id = rule_id
        self.severity = severity
        self.title = title
        self.description = description
        self.original = original
        self.suggested = suggested
        self.details = details or {}

    def to_dict(self):
        result = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
        }
        if self.original:
            result["original"] = self.original
        if self.suggested:
            result["suggested"] = self.suggested
        if self.details:
            result["details"] = self.details
        return result


class Rule:
    def check(self, parse_result: SQLParseResult, schema_info=None):
        raise NotImplementedError

    def _resolve_alias(self, column, table_aliases):
        parts = column.split(".")
        if len(parts) == 2:
            alias = parts[0]
            col = parts[1]
            if alias in table_aliases:
                return f"{table_aliases[alias]}.{col}"
        return column

    def _column_has_index(self, column, schema_info, table_name=None):
        if not schema_info:
            return False, None
        parts = column.split(".")
        tbl_name = parts[0] if len(parts) == 2 else table_name
        col_name = parts[-1] if len(parts) == 2 else parts[0]

        for table in schema_info.get("tables", []):
            if tbl_name and table.get("name") != tbl_name:
                continue
            for idx in table.get("indexes", []):
                idx_cols = idx.get("columns", [])
                if idx_cols and idx_cols[0] == col_name:
                    return True, idx
        return False, None

    def _all_columns_have_composite_index(self, columns, schema_info, table_name=None):
        if not schema_info:
            return False, None
        col_set = set(c.split(".")[-1] for c in columns)

        for table in schema_info.get("tables", []):
            if table_name and table.get("name") != table_name:
                continue
            for idx in table.get("indexes", []):
                idx_cols = idx.get("columns", [])
                if len(idx_cols) >= len(col_set) and col_set.issubset(set(idx_cols[: len(col_set)])):
                    return True, idx
        return False, None


class AvoidSelectStarRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.has_star:
            table_names = ", ".join(parse_result.tables)
            all_columns = []
            if schema_info and parse_result.tables:
                for table in schema_info.get("tables", []):
                    if table.get("name") in parse_result.tables:
                        for col in table.get("columns", []):
                            if isinstance(col, dict):
                                all_columns.append(col.get("name", ""))
            suggested_cols = ", ".join(all_columns) if all_columns else "<具体字段>"
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R001",
                    severity="high",
                    title="避免使用 SELECT *",
                    description=(
                        f"查询使用了 SELECT *，会导致不必要的数据传输和I/O开销。"
                        f"请将 * 替换为实际需要的字段列表。涉及表: {table_names}"
                    ),
                    original="SELECT *",
                    suggested=f"SELECT {suggested_cols} FROM {table_names}",
                    details={
                        "expandable": bool(all_columns),
                        "suggested_columns": all_columns,
                    },
                )
            )
        return suggestions


class AvoidFunctionOnWhereColumnRule(Rule):
    AGGREGATE_FUNCTIONS = frozenset([
        "SUM", "AVG", "COUNT", "MIN", "MAX", "GROUP_CONCAT",
        "STDDEV", "VARIANCE", "BIT_AND", "BIT_OR", "BIT_XOR",
    ])

    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if not parse_result.where_functions:
            return suggestions

        where_func_entries = []
        for func_entry in parse_result.where_functions:
            if isinstance(func_entry, dict):
                func_name = func_entry.get("function", "")
                if func_name in self.AGGREGATE_FUNCTIONS:
                    continue
                func_args = func_entry.get("arguments", "")
                full_expr = func_entry.get("full_expression", f"{func_name}({func_args})")
                arg_cols = [c.strip() for c in func_args.split(".")[-1].split(",")]
                where_func_entries.append({
                    "function": func_name,
                    "column": func_args,
                    "column_simple": arg_cols[0] if arg_cols else func_args,
                    "full_expression": full_expr,
                })

        if not where_func_entries:
            return suggestions

        if len(where_func_entries) == 1:
            entry = where_func_entries[0]
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R002",
                    severity="high",
                    title="避免在WHERE子句中对字段使用函数",
                    description=(
                        f"WHERE子句中对列 '{entry['column_simple']}' 使用了函数 {entry['function']}()，"
                        f"会导致数据库无法使用该列上的索引，触发全表扫描。"
                        f"建议将函数运算移到值侧，使字段保持裸列形式。"
                    ),
                    original=entry["full_expression"],
                    suggested=f"{entry['column_simple']} = {entry['function'].lower()}(...)",
                    details={
                        "function": entry["function"],
                        "column": entry["column_simple"],
                        "full_expression": entry["full_expression"],
                        "alternative": f"将 {entry['full_expression']} 改写为 {entry['column_simple']} = {entry['function'].lower()}(value)",
                        "impact": "索引失效，触发全表扫描",
                    },
                )
            )
        else:
            func_summary = "; ".join(
                f"{e['function']}({e['column_simple']})" for e in where_func_entries
            )
            detail_list = [
                {
                    "function": e["function"],
                    "column": e["column_simple"],
                    "full_expression": e["full_expression"],
                    "alternative": f"将 {e['full_expression']} 改写为 {e['column_simple']} = {e['function'].lower()}(value)",
                }
                for e in where_func_entries
            ]
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R002",
                    severity="high",
                    title=f"避免在WHERE子句中对{len(where_func_entries)}个字段使用函数",
                    description=(
                        f"WHERE子句中对多个列使用了函数 ({func_summary})，"
                        f"会导致这些列上的索引失效，触发全表扫描。"
                        f"建议将函数运算移到值侧，使字段保持裸列形式。"
                    ),
                    original="; ".join(e["full_expression"] for e in where_func_entries),
                    suggested="; ".join(
                        f"{e['column_simple']} = {e['function'].lower()}(...)" for e in where_func_entries
                    ),
                    details={
                        "functions": detail_list,
                        "affected_columns": [e["column_simple"] for e in where_func_entries],
                        "impact": "多个列索引失效，触发全表扫描",
                    },
                )
            )

        return suggestions


class InSubqueryToJoinRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        for in_subq in parse_result.in_subqueries:
            in_type = in_subq["type"]
            col = in_subq["column"]
            subq = in_subq["subquery"]
            original = in_subq["original"]

            subq_table_match = re.search(
                r'FROM\s+(\w+)', subq, re.IGNORECASE | re.DOTALL
            )
            subq_where_match = re.search(
                r'WHERE\s+(.+?)$', subq, re.IGNORECASE | re.DOTALL
            )

            subq_select_match = re.search(
                r'SELECT\s+(.+?)\s+FROM', subq, re.IGNORECASE | re.DOTALL
            )

            subq_table = subq_table_match.group(1) if subq_table_match else "subquery_table"
            subq_where = subq_where_match.group(1).strip() if subq_where_match else "1=1"
            subq_select_col = subq_select_match.group(1).strip() if subq_select_match else col

            subq_alias = f"{subq_table}_subq" if subq_table_match else "sq"

            join_version = self._build_join_version(
                parse_result, col, subq_table, subq_where, subq_alias, in_type
            )

            exists_version = self._build_exists_version(
                parse_result, col, subq_table, subq_where, subq_select_col, subq_alias, in_type
            )

            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R003",
                    severity="high",
                    title=f"将 {in_type} 子查询改写为 JOIN 或 EXISTS",
                    description=(
                        f"检测到 {in_type} 子查询，依赖临时表性能较差。"
                        f"JOIN 版本可消除临时表，EXISTS 版本在子查询表较大时更优。"
                    ),
                    original=original,
                    suggested=join_version,
                    details={
                        "in_type": in_type,
                        "column": col,
                        "subquery_table": subq_table,
                        "rewrite_options": [
                            {
                                "type": "JOIN改写",
                                "sql": join_version,
                                "best_for": "子查询结果集较小，主表较大时性能最佳",
                                "join_type": "INNER JOIN" if in_type == "IN" else "LEFT JOIN",
                            },
                            {
                                "type": "EXISTS改写",
                                "sql": exists_version,
                                "best_for": "子查询结果集很大，或存在重复数据时",
                                "operator": "EXISTS" if in_type == "IN" else "NOT EXISTS",
                            },
                        ],
                    },
                )
            )
        return suggestions

    def _build_join_version(self, parse_result, col, subq_table, subq_where, subq_alias, in_type):
        col_simple = col.split(".")[-1] if "." in col else col
        main_table = parse_result.tables[0] if parse_result.tables else "main_table"
        main_alias = [k for k, v in parse_result.table_aliases.items() if v == main_table]
        main_prefix = f"{main_alias[0]}." if main_alias else ""

        join_type = "INNER JOIN" if in_type == "IN" else "LEFT JOIN"
        extra_where = ""
        if in_type == "NOT_IN":
            extra_where = f" AND {subq_alias}.{col_simple} IS NULL"

        select_part = self._rebuild_select(parse_result, main_prefix, subq_alias)

        from_parts = []
        for tbl in parse_result.tables:
            alias = None
            for a, t in parse_result.table_aliases.items():
                if t == tbl:
                    alias = a
                    break
            if alias:
                from_parts.append(f"{tbl} {alias}")
            else:
                from_parts.append(tbl)
        from_clause = "FROM " + ", ".join(from_parts)

        other_where = self._extract_other_where_conditions(parse_result, col, in_type)

        join_clause = (
            f"{join_type} (SELECT DISTINCT {col_simple} FROM {subq_table} WHERE {subq_where}) {subq_alias} "
            f"ON {main_prefix}{col_simple} = {subq_alias}.{col_simple}"
        )

        sql_parts = [select_part, from_clause, join_clause]
        if other_where:
            sql_parts.append(f"WHERE {other_where}{extra_where}")
        elif extra_where:
            sql_parts.append(f"WHERE 1=1{extra_where}")

        if parse_result.order_by_columns:
            order_clause = ", ".join(parse_result.order_by_columns)
            sql_parts.append(f"ORDER BY {order_clause}")
        if parse_result.limit_value is not None:
            sql_parts.append(f"LIMIT {parse_result.limit_value}")

        return " ".join(sql_parts)

    def _build_exists_version(self, parse_result, col, subq_table, subq_where, subq_select_col, subq_alias, in_type):
        col_simple = col.split(".")[-1] if "." in col else col
        main_table = parse_result.tables[0] if parse_result.tables else "main_table"
        main_alias = [k for k, v in parse_result.table_aliases.items() if v == main_table]
        main_prefix = f"{main_alias[0]}." if main_alias else ""

        operator = "NOT EXISTS" if in_type == "NOT_IN" else "EXISTS"
        subq_select_col_simple = subq_select_col.split(".")[-1]

        select_part = self._rebuild_select(parse_result, main_prefix, None)

        from_parts = []
        for tbl in parse_result.tables:
            alias = None
            for a, t in parse_result.table_aliases.items():
                if t == tbl:
                    alias = a
                    break
            if alias:
                from_parts.append(f"{tbl} {alias}")
            else:
                from_parts.append(tbl)
        from_clause = "FROM " + ", ".join(from_parts)

        other_where = self._extract_other_where_conditions(parse_result, col, in_type)

        exists_clause = (
            f"{operator} ("
            f"SELECT 1 FROM {subq_table} {subq_alias} "
            f"WHERE {subq_alias}.{subq_select_col_simple} = {main_prefix}{col_simple} "
            f"AND {subq_where})"
        )

        where_parts = []
        if other_where:
            where_parts.append(other_where)
        where_parts.append(exists_clause)
        where_clause = "WHERE " + " AND ".join(where_parts)

        sql_parts = [select_part, from_clause, where_clause]
        if parse_result.order_by_columns:
            order_clause = ", ".join(parse_result.order_by_columns)
            sql_parts.append(f"ORDER BY {order_clause}")
        if parse_result.limit_value is not None:
            sql_parts.append(f"LIMIT {parse_result.limit_value}")

        return " ".join(sql_parts)

    def _extract_other_where_conditions(self, parse_result, in_col, in_type):
        if not parse_result.where_clause:
            return ""
        where_text = parse_result.where_clause
        where_text = re.sub(r'^WHERE\s+', '', where_text, flags=re.IGNORECASE).strip()

        for in_subq in parse_result.in_subqueries:
            original_text = in_subq["original"]
            where_text = where_text.replace(original_text, "")

        where_text = re.sub(r'\bAND\s+AND\b', 'AND', where_text, flags=re.IGNORECASE)
        where_text = re.sub(r'^\s*AND\s+', '', where_text, flags=re.IGNORECASE)
        where_text = re.sub(r'\s+AND\s*$', '', where_text, flags=re.IGNORECASE)
        where_text = where_text.strip()

        if where_text.startswith('(') and where_text.endswith(')'):
            where_text = where_text[1:-1].strip()

        return where_text

    def _rebuild_select(self, parse_result, main_prefix, subq_alias):
        cols = parse_result.columns
        if parse_result.has_star:
            if parse_result.tables:
                first_alias = [k for k, v in parse_result.table_aliases.items() if v == parse_result.tables[0]]
                prefix = f"{first_alias[0]}." if first_alias else ""
                return f"SELECT {prefix}*"
            return "SELECT *"
        else:
            select_cols = []
            for col in cols:
                if "." not in col and main_prefix:
                    select_cols.append(f"{main_prefix}{col}")
                else:
                    select_cols.append(col)
            if not select_cols:
                return "SELECT *"
            return f"SELECT {', '.join(select_cols)}"


class UseIndexForJoinRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.join_conditions and schema_info:
            for cond in parse_result.join_conditions:
                columns_in_cond = re.findall(r"(\w+\.\w+|\w+)", cond)
                for col in columns_in_cond:
                    resolved = self._resolve_alias(col, parse_result.table_aliases)
                    has_idx, idx_info = self._column_has_index(resolved, schema_info)
                    if not has_idx:
                        col_simple = resolved.split(".")[-1] if "." in resolved else col
                        table_hint = resolved.split(".")[0] if "." in resolved else (parse_result.tables[0] if parse_result.tables else "<table>")
                        suggestions.append(
                            OptimizationSuggestion(
                                rule_id="R004",
                                severity="high",
                                title="为JOIN关联列创建索引",
                                description=(
                                    f"JOIN条件 '{cond}' 中的列 '{resolved}' 没有索引。"
                                    f"未索引的关联列会导致嵌套循环全表扫描，性能下降明显。"
                                    f"建议为该列创建单列索引。"
                                ),
                                original=f"JOIN ... ON {cond}",
                                suggested=f"CREATE INDEX idx_{table_hint}_{col_simple} ON {table_hint}({col_simple});",
                                details={
                                    "column": resolved,
                                    "join_condition": cond,
                                    "index_type": "单列B树索引",
                                    "expected_benefit": "将嵌套循环从O(N²)降到O(N log M)",
                                },
                            )
                        )
        elif parse_result.join_conditions and not schema_info:
            for cond in parse_result.join_conditions:
                columns_in_cond = re.findall(r"(\w+\.\w+|\w+)", cond)
                for col in columns_in_cond:
                    suggestions.append(
                        OptimizationSuggestion(
                            rule_id="R004",
                            severity="low",
                            title="确认关联列是否有索引",
                            description=(
                                f"JOIN条件 '{cond}' 中的列 '{col}' "
                                f"请确认是否已有索引。未提供表结构信息，无法自动判断。"
                            ),
                            original=f"JOIN ... ON {cond}",
                            suggested="提供表结构信息以获取更精准建议",
                            details={"column": col, "join_condition": cond},
                        )
                    )
        return suggestions


class SuggestIndexForWhereRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_columns and schema_info:
            all_where_cols = list(parse_result.where_columns)
            composite_suggested = False

            if len(all_where_cols) >= 2:
                has_comp_idx, comp_idx = self._all_columns_have_composite_index(
                    all_where_cols, schema_info, parse_result.tables[0] if parse_result.tables else None
                )
                if not has_comp_idx:
                    main_table = parse_result.tables[0] if parse_result.tables else "unknown_table"
                    idx_cols = ", ".join(all_where_cols)
                    idx_name = f"idx_{main_table}_{'_'.join(all_where_cols)}"
                    suggestions.append(
                        OptimizationSuggestion(
                            rule_id="R005",
                            severity="medium",
                            title="建议为WHERE条件创建联合索引",
                            description=(
                                f"WHERE子句中使用了 {len(all_where_cols)} 个列过滤 ({idx_cols})。"
                                f"创建联合索引比创建多个单列索引效率更高，数据库可直接利用索引完成多条件过滤。"
                            ),
                            original=f"WHERE {idx_cols.replace(',', ' AND ')}",
                            suggested=f"CREATE INDEX {idx_name} ON {main_table}({idx_cols});",
                            details={
                                "columns": all_where_cols,
                                "index_type": "联合B树索引",
                                "is_composite": True,
                                "leftmost_prefix": all_where_cols[0],
                                "best_for": "多个等值条件组合过滤时",
                                "alternative": (
                                    f"如查询模式多变，可分别为单列创建索引: "
                                    + "; ".join(
                                        f"CREATE INDEX idx_{main_table}_{c} ON {main_table}({c})"
                                        for c in all_where_cols
                                    )
                                ),
                            },
                        )
                    )
                    composite_suggested = True

            if not composite_suggested:
                for col in all_where_cols:
                    resolved = self._resolve_alias(col, parse_result.table_aliases)
                    has_idx, idx_info = self._column_has_index(resolved, schema_info)
                    if not has_idx:
                        col_simple = resolved.split(".")[-1] if "." in resolved else col
                        table_hint = resolved.split(".")[0] if "." in resolved else (parse_result.tables[0] if parse_result.tables else "<table>")

                        already_suggested = any(
                            s.details.get("column") == resolved for s in suggestions
                        )
                        if not already_suggested:
                            suggestions.append(
                                OptimizationSuggestion(
                                    rule_id="R005",
                                    severity="medium",
                                    title="为WHERE过滤列创建索引",
                                    description=(
                                        f"WHERE子句中的列 '{resolved}' 没有索引。"
                                        f"为频繁过滤的列创建索引可以大幅提升查询性能。"
                                    ),
                                    original=f"WHERE {resolved} = ...",
                                    suggested=f"CREATE INDEX idx_{table_hint}_{col_simple} ON {table_hint}({col_simple});",
                                    details={
                                        "column": resolved,
                                        "index_type": "单列B树索引",
                                        "is_composite": False,
                                    },
                                )
                            )
        elif parse_result.where_columns and not schema_info:
            cols_str = ", ".join(parse_result.where_columns)
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R005",
                    severity="low",
                    title="确认WHERE过滤列是否有索引",
                    description=(
                        f"WHERE子句使用了列 ({cols_str})，"
                        f"请确认这些列是否已有索引。未提供表结构信息，无法自动判断。"
                    ),
                    original=f"WHERE {cols_str}",
                    suggested="提供表结构信息以获取更精准的索引建议",
                    details={"columns": parse_result.where_columns},
                )
            )
        return suggestions


class SuggestIndexForOrderByRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.order_by_columns and schema_info:
            if len(parse_result.order_by_columns) >= 2:
                has_comp_idx, _ = self._all_columns_have_composite_index(
                    parse_result.order_by_columns, schema_info,
                    parse_result.tables[0] if parse_result.tables else None
                )
                if not has_comp_idx:
                    main_table = parse_result.tables[0] if parse_result.tables else "unknown_table"
                    idx_cols = ", ".join(parse_result.order_by_columns)
                    idx_name = f"idx_{main_table}_order_{'_'.join(parse_result.order_by_columns)}"
                    suggestions.append(
                        OptimizationSuggestion(
                            rule_id="R006",
                            severity="medium",
                            title="为ORDER BY创建联合索引以消除排序",
                            description=(
                                f"ORDER BY 使用了 {len(parse_result.order_by_columns)} 个列 ({idx_cols})。"
                                f"创建匹配排序顺序的联合索引可直接利用索引顺序，避免文件排序(filesort)。"
                            ),
                            original=f"ORDER BY {idx_cols}",
                            suggested=f"CREATE INDEX {idx_name} ON {main_table}({idx_cols});",
                            details={
                                "columns": parse_result.order_by_columns,
                                "index_type": "联合B树索引",
                                "is_composite": True,
                                "benefit": "消除 filesort，避免内存/磁盘排序",
                            },
                        )
                    )

            for col in parse_result.order_by_columns:
                resolved = self._resolve_alias(col, parse_result.table_aliases)
                has_idx, idx_info = self._column_has_index(col, schema_info)
                if not has_idx:
                    col_simple = col.split(".")[-1] if "." in col else col
                    table_hint = col.split(".")[0] if "." in col else (parse_result.tables[0] if parse_result.tables else "<table>")
                    suggestions.append(
                        OptimizationSuggestion(
                            rule_id="R006",
                            severity="low",
                            title="为ORDER BY列创建索引",
                            description=(
                                f"ORDER BY列 '{col}' 没有索引，可能导致文件排序(filesort)。"
                                f"考虑为排序列创建索引以避免额外的排序操作。"
                            ),
                            original=f"ORDER BY {col}",
                            suggested=f"CREATE INDEX idx_{table_hint}_{col_simple} ON {table_hint}({col_simple});",
                            details={
                                "column": col,
                                "index_type": "单列B树索引",
                                "risk": "filesort 可能使用磁盘临时表",
                            },
                        )
                    )
        return suggestions


class AvoidLeadingWildcardLikeRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_clause:
            leading_wildcard = re.findall(
                r"LIKE\s+'%[^']*'", parse_result.where_clause, re.IGNORECASE
            )
            for pattern in leading_wildcard:
                col_match = re.search(r'(\w+\.?\w*)\s+LIKE', parse_result.where_clause, re.IGNORECASE)
                col = col_match.group(1) if col_match else "unknown_column"

                col_type = None
                if schema_info:
                    for table in schema_info.get("tables", []):
                        for col_def in table.get("columns", []):
                            if isinstance(col_def, dict) and col_def.get("name") == col.split(".")[-1]:
                                col_type = col_def.get("type", "")

                suggestions.append(
                    OptimizationSuggestion(
                        rule_id="R007",
                        severity="high",
                        title="避免LIKE前缀通配符",
                        description=(
                            f"LIKE模式 '{pattern}' 以通配符%开头，"
                            f"会导致B树索引失效，触发全表扫描。"
                        ),
                        original=f"WHERE ... {pattern} ...",
                        suggested="WHERE ... LIKE 'prefix%' ... 或使用全文索引",
                        details={
                            "column": col,
                            "column_type": col_type,
                            "pattern": pattern,
                            "alternatives": [
                                "将列存储为反向字符串，使用 LIKE 'x%' 替代",
                                "创建 FULLTEXT 全文索引，使用 MATCH AGAINST 语法",
                                "使用搜索引擎（如 Elasticsearch）处理模糊搜索",
                            ],
                        },
                    )
                )
        return suggestions


class SuggestLimitRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.limit_value is None and not parse_result.where_clause:
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R008",
                    severity="medium",
                    title="为无WHERE的全表查询添加LIMIT",
                    description=(
                        "查询没有WHERE条件且没有LIMIT限制，可能返回大量数据。"
                        "建议添加LIMIT子句限制返回行数，避免网络拥塞和内存溢出。"
                    ),
                    original="SELECT ... FROM ...",
                    suggested="SELECT ... FROM ... LIMIT <n>",
                    details={
                        "default_suggestion": "LIMIT 100",
                        "risk": "无限制查询可能导致数据库和应用OOM",
                    },
                )
            )
        return suggestions


class SuggestDistinctRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.distinct:
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R009",
                    severity="low",
                    title="谨慎使用DISTINCT",
                    description=(
                        "DISTINCT会导致数据库执行排序去重操作，开销较大。"
                        "建议检查是否可以通过优化查询逻辑（如使用GROUP BY或更精确的JOIN条件）来避免去重。"
                    ),
                    original="SELECT DISTINCT ...",
                    suggested="SELECT ... (优化JOIN/WHERE条件消除重复)",
                    details={
                        "alternative": "使用 EXISTS 或半连接替代 JOIN + DISTINCT",
                        "overhead": "需要额外的排序操作 O(N log N)",
                    },
                )
            )
        return suggestions


class OrToUnionRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_clause:
            or_count = len(re.findall(r'\bOR\b', parse_result.where_clause, re.IGNORECASE))
            if or_count >= 2:
                suggestions.append(
                    OptimizationSuggestion(
                        rule_id="R010",
                        severity="medium",
                        title="考虑将多个OR条件改写为UNION ALL",
                        description=(
                            f"WHERE子句中包含{or_count}个OR条件。"
                            f"多个OR条件可能导致优化器无法有效利用索引。"
                            f"当每个OR分支可以独立利用索引时，UNION ALL通常性能更好。"
                        ),
                        original="WHERE cond1 OR cond2 OR cond3",
                        suggested="SELECT ... WHERE cond1 UNION ALL SELECT ... WHERE cond2 ...",
                        details={
                            "or_count": or_count,
                            "note": "请确认每个分支结果无重复，否则需要用 UNION",
                            "when_to_use": "每个OR分支可独立走索引时性能最佳",
                        },
                    )
                )
        return suggestions


class AvoidNotInRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        has_not_in = False
        if parse_result.where_clause:
            not_in_matches = re.findall(
                r'NOT\s+IN\s*\(', parse_result.where_clause, re.IGNORECASE
            )
            has_not_in = bool(not_in_matches)

        if has_not_in and not parse_result.in_subqueries:
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R011",
                    severity="high",
                    title="避免使用NOT IN，改用LEFT JOIN或NOT EXISTS",
                    description=(
                        "NOT IN 子查询在处理NULL值时行为不符合直觉，且性能通常不如"
                        "LEFT JOIN ... WHERE ... IS NULL 或 NOT EXISTS。"
                    ),
                    original="WHERE col NOT IN (SELECT ...)",
                    suggested="WHERE NOT EXISTS (SELECT 1 FROM ... WHERE ...) 或 LEFT JOIN ... IS NULL",
                    details={
                        "null_behavior": "NOT IN 遇到NULL时整体条件永假，可能返回空结果",
                        "recommended": "NOT EXISTS (语义更清晰，性能更稳定)",
                    },
                )
            )
        return suggestions


class CheckColumnTypeRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if not schema_info:
            return suggestions

        where_text = parse_result.where_clause or ""

        for col in parse_result.where_columns:
            resolved = self._resolve_alias(col, parse_result.table_aliases)
            col_type = self._get_column_type(resolved, schema_info)
            if col_type:
                col_simple = resolved.split(".")[-1] if "." in resolved else resolved
                if re.search(r'INT|BIGINT|SMALLINT|TINYINT|FLOAT|DOUBLE|DECIMAL|NUMERIC', col_type, re.IGNORECASE):
                    string_compare = re.search(
                        rf'{col_simple}\s*=\s*[\'"][^\'"]+[\'"]', where_text, re.IGNORECASE
                    )
                    if string_compare:
                        suggestions.append(
                            OptimizationSuggestion(
                                rule_id="R012",
                                severity="medium",
                                title="避免隐式类型转换",
                                description=(
                                    f"列 '{resolved}' 类型为 {col_type}，但WHERE子句中使用了字符串比较。"
                                    f"隐式类型转换会导致索引失效。"
                                ),
                                original=f"WHERE {resolved} = 'string_value'",
                                suggested=f"WHERE {resolved} = numeric_value",
                                details={
                                    "column": resolved,
                                    "column_type": col_type,
                                    "risk": "索引失效，触发全表扫描",
                                },
                            )
                        )
        return suggestions

    def _get_column_type(self, column, schema_info):
        if not schema_info:
            return None
        parts = column.split(".")
        table_name = parts[0] if len(parts) == 2 else None
        col_name = parts[-1] if len(parts) == 2 else parts[0]

        for table in schema_info.get("tables", []):
            if table_name and table.get("name") != table_name:
                continue
            for col_def in table.get("columns", []):
                if isinstance(col_def, dict):
                    if col_def.get("name") == col_name:
                        return col_def.get("type", "")
                elif isinstance(col_def, str):
                    if col_def == col_name:
                        return ""
        return None


class HavingWithoutGroupByRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.having_clause and not parse_result.group_by_columns:
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R013",
                    severity="medium",
                    title="HAVING子句应配合GROUP BY使用",
                    description=(
                        "检测到HAVING子句但没有GROUP BY，HAVING主要用于过滤聚合结果。"
                        "普通条件建议移到WHERE子句中执行，可在聚合前过滤数据，性能更好。"
                    ),
                    original="SELECT ... HAVING ...",
                    suggested="SELECT ... WHERE <普通条件> GROUP BY ... HAVING <聚合条件>",
                    details={
                        "having_clause": parse_result.having_clause,
                        "performance_hint": "WHERE在聚合前过滤，HAVING在聚合后过滤",
                    },
                )
            )
        return suggestions


class RuleEngine:
    def __init__(self):
        self.parser = SQLParser()
        self.rules = [
            AvoidSelectStarRule(),
            AvoidFunctionOnWhereColumnRule(),
            InSubqueryToJoinRule(),
            UseIndexForJoinRule(),
            SuggestIndexForWhereRule(),
            SuggestIndexForOrderByRule(),
            AvoidLeadingWildcardLikeRule(),
            SuggestLimitRule(),
            SuggestDistinctRule(),
            OrToUnionRule(),
            AvoidNotInRule(),
            CheckColumnTypeRule(),
            HavingWithoutGroupByRule(),
        ]

    def analyze(self, sql: str, schema_info=None):
        parse_result = self.parser.parse(sql)
        all_suggestions = []

        if parse_result.statement_type != "SELECT":
            return {
                "sql": sql,
                "statement_type": parse_result.statement_type,
                "suggestions": [],
                "parse_result": self._parse_result_to_dict(parse_result),
                "message": f"当前仅支持SELECT语句分析，检测到类型: {parse_result.statement_type}",
            }

        for rule in self.rules:
            try:
                suggestions = rule.check(parse_result, schema_info)
                all_suggestions.extend(suggestions)
            except Exception as e:
                all_suggestions.append(
                    OptimizationSuggestion(
                        rule_id="ERR",
                        severity="low",
                        title="规则执行异常",
                        description=f"规则 {rule.__class__.__name__} 执行出错: {str(e)}",
                    )
                )

        severity_order = {"high": 0, "medium": 1, "low": 2}
        all_suggestions.sort(key=lambda s: severity_order.get(s.severity, 3))

        self._last_rewrite_actions = []
        optimized_sql = self._generate_optimized_sql(
            sql, all_suggestions, parse_result, schema_info
        )

        summary = self._build_summary(all_suggestions)
        if self._last_rewrite_actions:
            summary["rewrite_actions"] = self._last_rewrite_actions
            for action in self._last_rewrite_actions:
                if action == "expanded_star":
                    summary["expanded_star"] = True
                elif action == "added_limit":
                    summary["added_limit"] = True
                elif action == "rewrote_in_to_exists":
                    summary["rewrote_in_to_exists"] = True

        return {
            "sql": sql,
            "statement_type": parse_result.statement_type,
            "suggestions": [s.to_dict() for s in all_suggestions],
            "parse_result": self._parse_result_to_dict(parse_result),
            "optimized_sql": optimized_sql,
            "summary": summary,
        }

    def _parse_result_to_dict(self, parse_result: SQLParseResult):
        return {
            "tables": parse_result.tables,
            "columns": parse_result.columns,
            "has_star": parse_result.has_star,
            "where_columns": parse_result.where_columns,
            "where_functions": parse_result.where_functions,
            "subqueries_count": len(parse_result.subqueries),
            "in_subqueries": parse_result.in_subqueries,
            "join_types": parse_result.join_types,
            "join_conditions": parse_result.join_conditions,
            "order_by_columns": parse_result.order_by_columns,
            "group_by_columns": parse_result.group_by_columns,
            "having_columns": parse_result.having_columns,
            "having_clause": parse_result.having_clause,
            "has_having": parse_result.having_clause is not None,
            "has_group_by": len(parse_result.group_by_columns) > 0,
            "has_order_by": len(parse_result.order_by_columns) > 0,
            "limit_value": parse_result.limit_value,
            "distinct": parse_result.distinct,
            "table_aliases": parse_result.table_aliases,
            "aggregate_functions": parse_result.aggregate_functions,
            "union_count": parse_result.union_count,
        }

    def _generate_optimized_sql(self, original_sql, suggestions, parse_result, schema_info):
        optimized = original_sql.strip()
        rewrite_actions = []

        if parse_result.has_star:
            all_columns = []
            if schema_info and parse_result.tables:
                for table in schema_info.get("tables", []):
                    if table.get("name") in parse_result.tables:
                        for col in table.get("columns", []):
                            if isinstance(col, dict):
                                col_name = col.get("name", "")
                                table_prefix = f"{table.get('name')}." if len(parse_result.tables) > 1 else ""
                                all_columns.append(f"{table_prefix}{col_name}")
            if all_columns:
                cols_str = ", ".join(all_columns)
                optimized = re.sub(
                    r'SELECT\s+(DISTINCT\s+)?\*',
                    lambda m: f"SELECT {m.group(1) or ''}{cols_str}",
                    optimized,
                    count=1,
                    flags=re.IGNORECASE,
                )
                rewrite_actions.append("expanded_star")

        if parse_result.limit_value is None:
            if not re.search(r'\bLIMIT\b', optimized, re.IGNORECASE):
                optimized = f"{optimized.rstrip(';')} LIMIT 100"
                rewrite_actions.append("added_limit")

        for in_subq in parse_result.in_subqueries:
            if in_subq["type"] == "IN":
                original_text = in_subq["original"]
                col = in_subq["column"]
                subq = in_subq["subquery"]
                col_simple = col.split(".")[-1] if "." in col else col

                subq_table_match = re.search(r'FROM\s+(\w+)', subq, re.IGNORECASE | re.DOTALL)
                subq_where_match = re.search(r'WHERE\s+(.+?)$', subq, re.IGNORECASE | re.DOTALL)
                subq_select_match = re.search(r'SELECT\s+(.+?)\s+FROM', subq, re.IGNORECASE | re.DOTALL)

                if subq_table_match and subq_where_match and subq_select_match:
                    subq_table = subq_table_match.group(1)
                    subq_where = subq_where_match.group(1).strip()
                    subq_select_col = subq_select_match.group(1).strip()
                    subq_select_simple = subq_select_col.split(".")[-1]
                    main_alias = (
                        list(parse_result.table_aliases.keys())[0]
                        if parse_result.table_aliases
                        else None
                    )
                    main_prefix = f"{main_alias}." if main_alias else ""

                    replacement = (
                        f"EXISTS (SELECT 1 FROM {subq_table} sq "
                        f"WHERE sq.{subq_select_simple} = {main_prefix}{col_simple} "
                        f"AND {subq_where})"
                    )
                    optimized = optimized.replace(original_text, replacement, 1)
                    rewrite_actions.append("rewrote_in_to_exists")

        self._last_rewrite_actions = rewrite_actions
        return optimized

    def _build_summary(self, suggestions):
        categories = {
            "performance": [],
            "index": [],
            "rewrite": [],
            "style": [],
        }

        for s in suggestions:
            rid = s.rule_id
            if rid in ("R001", "R002", "R007", "R010", "R011", "R012"):
                categories["performance"].append(rid)
            elif rid in ("R004", "R005", "R006"):
                categories["index"].append(rid)
            elif rid in ("R003", "R008", "R013"):
                categories["rewrite"].append(rid)
            elif rid in ("R009",):
                categories["style"].append(rid)

        return {
            "total_issues": len(suggestions),
            "by_severity": {
                "high": sum(1 for s in suggestions if s.severity == "high"),
                "medium": sum(1 for s in suggestions if s.severity == "medium"),
                "low": sum(1 for s in suggestions if s.severity == "low"),
            },
            "by_category": categories,
        }
