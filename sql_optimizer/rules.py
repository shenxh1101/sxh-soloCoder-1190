import re
from .parser import SQLParser, SQLParseResult


class OptimizationSuggestion:
    def __init__(self, rule_id, severity, title, description, original=None, suggested=None):
        self.rule_id = rule_id
        self.severity = severity
        self.title = title
        self.description = description
        self.original = original
        self.suggested = suggested

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


class AvoidSelectStarRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.has_star:
            table_names = ", ".join(parse_result.tables)
            suggestions.append(
                OptimizationSuggestion(
                    rule_id="R001",
                    severity="high",
                    title="避免使用 SELECT *",
                    description=(
                        f"查询使用了 SELECT *，会导致不必要的数据传输和I/O开销。"
                        f"请将 * 替换为实际需要的字段列表。"
                        f"涉及表: {table_names}"
                    ),
                    original="SELECT *",
                    suggested=f"SELECT <具体字段> FROM {table_names}",
                )
            )
        return suggestions


class AvoidFunctionOnWhereColumnRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_functions:
            for func_name in parse_result.where_functions:
                suggestions.append(
                    OptimizationSuggestion(
                        rule_id="R002",
                        severity="high",
                        title="避免在WHERE子句中对字段使用函数",
                        description=(
                            f"在WHERE子句中使用函数 {func_name}() 会导致数据库无法使用该列上的索引，"
                            f"触发全表扫描。建议改写查询逻辑，使字段保持裸列形式。"
                        ),
                        original=f"WHERE {func_name.lower()}(column) = ...",
                        suggested="WHERE column = {func_name}_result",
                    )
                )
        return suggestions


class SubqueryToJoinRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.subqueries:
            for i, sq in enumerate(parse_result.subqueries, 1):
                suggestions.append(
                    OptimizationSuggestion(
                        rule_id="R003",
                        severity="medium",
                        title="将子查询改写为JOIN",
                        description=(
                            f"检测到第{i}个子查询。子查询通常性能较差，因为可能产生临时表。"
                            f"建议将子查询改写为JOIN操作，以获得更好的执行计划。"
                        ),
                        original=f"... (SELECT ... ) ...",
                        suggested="... JOIN ... ON ...",
                    )
                )
        return suggestions


class UseIndexForJoinRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.join_conditions and schema_info:
            for cond in parse_result.join_conditions:
                columns_in_cond = re.findall(r"(\w+\.\w+|\w+)", cond)
                for col in columns_in_cond:
                    resolved = self._resolve_alias(col, parse_result.table_aliases)
                    if not self._column_has_index(resolved, schema_info):
                        suggestions.append(
                            OptimizationSuggestion(
                                rule_id="R004",
                                severity="medium",
                                title="使用索引列进行关联",
                                description=(
                                    f"JOIN条件 '{cond}' 中的列 '{resolved}' 没有索引。"
                                    f"未索引的关联列会导致嵌套循环全表扫描，性能低下。"
                                    f"建议为该列创建索引。"
                                ),
                                original=f"JOIN ... ON {cond}",
                                suggested=f"CREATE INDEX idx_{col.replace('.', '_')} ON ...({col.split('.')[-1]});",
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
                        )
                    )
        return suggestions

    def _column_has_index(self, column, schema_info):
        if not schema_info:
            return False
        parts = column.split(".")
        table_name = parts[0] if len(parts) == 2 else None
        col_name = parts[-1] if len(parts) == 2 else parts[0]

        for table in schema_info.get("tables", []):
            if table_name and table.get("name") != table_name:
                continue
            for idx in table.get("indexes", []):
                if col_name in idx.get("columns", []):
                    return True
        return False


class SuggestIndexForWhereRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_columns and schema_info:
            for col in parse_result.where_columns:
                resolved = self._resolve_alias(col, parse_result.table_aliases)
                if not self._column_has_index(resolved, schema_info):
                    col_simple = resolved.split(".")[-1] if "." in resolved else resolved
                    table_hint = resolved.split(".")[0] if "." in resolved else (parse_result.tables[0] if parse_result.tables else "<table>")
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
                            suggested=f"CREATE INDEX idx_{col_simple} ON {table_hint}({col_simple});",
                        )
                    )
        return suggestions

    def _column_has_index(self, column, schema_info):
        if not schema_info:
            return False
        parts = column.split(".")
        table_name = parts[0] if len(parts) == 2 else None
        col_name = parts[-1] if len(parts) == 2 else parts[0]

        for table in schema_info.get("tables", []):
            if table_name and table.get("name") != table_name:
                continue
            for idx in table.get("indexes", []):
                if col_name in idx.get("columns", []):
                    return True
        return False


class SuggestIndexForOrderByRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.order_by_columns and schema_info:
            for col in parse_result.order_by_columns:
                if not self._column_has_index(col, schema_info):
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
                            suggested=f"CREATE INDEX idx_{col_simple} ON {table_hint}({col_simple});",
                        )
                    )
        return suggestions

    def _column_has_index(self, column, schema_info):
        if not schema_info:
            return False
        parts = column.split(".")
        table_name = parts[0] if len(parts) == 2 else None
        col_name = parts[-1] if len(parts) == 2 else parts[0]
        for table in schema_info.get("tables", []):
            if table_name and table.get("name") != table_name:
                continue
            for idx in table.get("indexes", []):
                if col_name in idx.get("columns", []):
                    return True
        return False


class AvoidLeadingWildcardLikeRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_clause:
            leading_wildcard = re.findall(
                r"LIKE\s+'%[^']*'", parse_result.where_clause, re.IGNORECASE
            )
            for pattern in leading_wildcard:
                suggestions.append(
                    OptimizationSuggestion(
                        rule_id="R007",
                        severity="high",
                        title="避免LIKE前缀通配符",
                        description=(
                            f"LIKE模式 '{pattern}' 以通配符%开头，"
                            f"会导致索引失效，触发全表扫描。"
                            f"建议使用全文索引或调整查询模式。"
                        ),
                        original=f"WHERE ... {pattern} ...",
                        suggested="WHERE ... LIKE 'prefix%' ... 或使用全文索引",
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
                        "建议添加LIMIT子句限制返回行数。"
                    ),
                    original="SELECT ... FROM ...",
                    suggested="SELECT ... FROM ... LIMIT <n>",
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
                    )
                )
        return suggestions


class AvoidNotInRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if parse_result.where_clause:
            not_in_matches = re.findall(
                r'NOT\s+IN\s*\(', parse_result.where_clause, re.IGNORECASE
            )
            if not_in_matches:
                suggestions.append(
                    OptimizationSuggestion(
                        rule_id="R011",
                        severity="medium",
                        title="避免使用NOT IN，改用LEFT JOIN或NOT EXISTS",
                        description=(
                            "NOT IN 子查询在处理NULL值时行为不符合直觉，且性能通常不如"
                            "LEFT JOIN ... WHERE ... IS NULL 或 NOT EXISTS。"
                        ),
                        original="WHERE col NOT IN (SELECT ...)",
                        suggested="WHERE NOT EXISTS (SELECT 1 FROM ... WHERE ...) 或 LEFT JOIN ... IS NULL",
                    )
                )
        return suggestions


class CheckColumnTypeRule(Rule):
    def check(self, parse_result: SQLParseResult, schema_info=None):
        suggestions = []
        if not schema_info:
            return suggestions

        where_text = parse_result.where_clause or ""
        implicit_patterns = [
            (r"=\s*'", "字符串与数字隐式转换", "WHERE col = '123' 而col为数值类型"),
            (r"=\s*\d+\s*$", "数字与字符串隐式转换", "WHERE col = 123 而col为字符串类型"),
        ]

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


class RuleEngine:
    def __init__(self):
        self.parser = SQLParser()
        self.rules = [
            AvoidSelectStarRule(),
            AvoidFunctionOnWhereColumnRule(),
            SubqueryToJoinRule(),
            UseIndexForJoinRule(),
            SuggestIndexForWhereRule(),
            SuggestIndexForOrderByRule(),
            AvoidLeadingWildcardLikeRule(),
            SuggestLimitRule(),
            SuggestDistinctRule(),
            OrToUnionRule(),
            AvoidNotInRule(),
            CheckColumnTypeRule(),
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

        return {
            "sql": sql,
            "statement_type": parse_result.statement_type,
            "suggestions": [s.to_dict() for s in all_suggestions],
            "parse_result": self._parse_result_to_dict(parse_result),
            "optimized_sql": self._generate_optimized_sql(sql, all_suggestions, parse_result),
        }

    def _parse_result_to_dict(self, parse_result: SQLParseResult):
        return {
            "tables": parse_result.tables,
            "columns": parse_result.columns,
            "has_star": parse_result.has_star,
            "where_columns": parse_result.where_columns,
            "where_functions": parse_result.where_functions,
            "subqueries_count": len(parse_result.subqueries),
            "join_types": parse_result.join_types,
            "join_conditions": parse_result.join_conditions,
            "order_by_columns": parse_result.order_by_columns,
            "group_by_columns": parse_result.group_by_columns,
            "limit_value": parse_result.limit_value,
            "distinct": parse_result.distinct,
            "table_aliases": parse_result.table_aliases,
        }

    def _generate_optimized_sql(self, original_sql, suggestions, parse_result):
        optimized = original_sql.strip()

        if parse_result.has_star and parse_result.columns:
            table_prefix = f"{parse_result.tables[0]}." if parse_result.tables else ""
            cols = ", ".join(parse_result.columns) if parse_result.columns else "<具体字段>"
            optimized = re.sub(
                r'SELECT\s+\*',
                f'SELECT {cols}',
                optimized,
                count=1,
                flags=re.IGNORECASE
            )

        return optimized
