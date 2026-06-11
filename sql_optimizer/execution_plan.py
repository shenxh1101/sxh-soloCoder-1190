import re
import sqlparse
from .parser import SQLParser


class PlanNode:
    def __init__(self, node_type, table=None, alias=None, access_type="ALL", key=None, rows=None, extra=None, children=None):
        self.node_type = node_type
        self.table = table
        self.alias = alias
        self.access_type = access_type
        self.key = key
        self.rows = rows
        self.extra = extra or ""
        self.children = children or []

    def to_dict(self):
        result = {
            "type": self.node_type,
            "access_type": self.access_type,
        }
        if self.table:
            result["table"] = self.table
        if self.alias:
            result["alias"] = self.alias
        if self.key:
            result["key"] = self.key
        if self.rows:
            result["rows"] = self.rows
        if self.extra:
            result["extra"] = self.extra
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        return result

    def to_text(self, indent=0):
        prefix = "  " * indent
        arrow = "-> " if indent > 0 else ""
        parts = [f"{prefix}{arrow}{self.node_type}"]

        if self.table:
            parts.append(f" on {self.table}")
            if self.alias and self.alias != self.table:
                parts.append(f" ({self.alias})")

        parts.append(f" [{self.access_type}]")

        if self.key:
            parts.append(f" key={self.key}")

        if self.rows:
            parts.append(f" rows≈{self.rows}")

        if self.extra:
            parts.append(f" ({self.extra})")

        line = "".join(parts)
        child_lines = [c.to_text(indent + 1) for c in self.children]
        return "\n".join([line] + child_lines)


class ExecutionPlanGenerator:
    def __init__(self):
        self.parser = SQLParser()

    def generate(self, sql: str, schema_info=None):
        parse_result = self.parser.parse(sql)

        if parse_result.statement_type != "SELECT":
            return {
                "plan_text": f"不支持的语句类型: {parse_result.statement_type}",
                "plan_tree": None,
            }

        root = self._build_plan(parse_result, schema_info)

        return {
            "plan_text": root.to_text(),
            "plan_tree": root.to_dict(),
        }

    def _build_plan(self, parse_result, schema_info):
        has_group = bool(parse_result.group_by_columns)
        has_aggregate = bool(re.search(
            r'\b(SUM|AVG|COUNT|MIN|MAX|GROUP_CONCAT)\s*\(',
            parse_result.raw_sql,
            re.IGNORECASE,
        ))

        tables = parse_result.tables
        if not tables:
            return PlanNode("SIMPLE_QUERY", access_type="NONE")

        if len(tables) == 1:
            return self._build_single_table_plan(parse_result, schema_info)

        return self._build_join_plan(parse_result, schema_info, has_group, has_aggregate)

    def _build_single_table_plan(self, parse_result, schema_info):
        table = parse_result.tables[0]
        alias = None
        for a, t in parse_result.table_aliases.items():
            if t == table:
                alias = a
                break

        access_type, key, rows, extra = self._determine_access(
            table, parse_result, schema_info
        )

        children = []

        if parse_result.where_clause:
            where_node = PlanNode(
                "FILTER",
                extra=f"WHERE条件过滤: {self._summarize_where(parse_result.where_clause)}",
            )
            children.append(where_node)

        if parse_result.group_by_columns:
            group_node = PlanNode(
                "AGGREGATE",
                extra=f"GROUP BY: {', '.join(parse_result.group_by_columns)}",
            )
            children.append(group_node)
        elif has_aggregate_check(parse_result.raw_sql):
            group_node = PlanNode(
                "AGGREGATE",
                extra="全局聚合",
            )
            children.append(group_node)

        if parse_result.order_by_columns:
            order_node = PlanNode(
                "SORT",
                extra=f"ORDER BY: {', '.join(parse_result.order_by_columns)}",
            )
            children.append(order_node)

        if parse_result.limit_value:
            limit_node = PlanNode(
                "LIMIT",
                rows=parse_result.limit_value,
                extra=f"LIMIT {parse_result.limit_value}",
            )
            children.append(limit_node)

        root = PlanNode(
            "TABLE_SCAN",
            table=table,
            alias=alias,
            access_type=access_type,
            key=key,
            rows=rows,
            extra=extra,
            children=children,
        )

        return root

    def _build_join_plan(self, parse_result, schema_info, has_group, has_aggregate):
        tables = parse_result.tables
        join_types = parse_result.join_types

        join_nodes = []
        for i, table in enumerate(tables):
            alias = None
            for a, t in parse_result.table_aliases.items():
                if t == table:
                    alias = a
                    break

            access_type, key, rows, extra = self._determine_access(
                table, parse_result, schema_info
            )

            if i == 0:
                node_type = "DRIVING_TABLE"
                extra = f"驱动表 {extra}"
            else:
                node_type = "JOINED_TABLE"
                join_type = join_types[i - 1] if i - 1 < len(join_types) else "INNER JOIN"
                cond = (
                    parse_result.join_conditions[i - 1]
                    if i - 1 < len(parse_result.join_conditions)
                    else "N/A"
                )
                extra = f"{join_type} ON {cond}"

            join_nodes.append(
                PlanNode(
                    node_type,
                    table=table,
                    alias=alias,
                    access_type=access_type,
                    key=key,
                    rows=rows,
                    extra=extra,
                )
            )

        children = list(join_nodes)

        if parse_result.where_clause:
            where_node = PlanNode(
                "FILTER",
                extra=f"WHERE条件过滤: {self._summarize_where(parse_result.where_clause)}",
            )
            children.append(where_node)

        if parse_result.group_by_columns:
            group_node = PlanNode(
                "AGGREGATE",
                extra=f"GROUP BY: {', '.join(parse_result.group_by_columns)}",
            )
            children.append(group_node)
        elif has_aggregate:
            group_node = PlanNode(
                "AGGREGATE",
                extra="全局聚合",
            )
            children.append(group_node)

        if parse_result.order_by_columns:
            order_node = PlanNode(
                "SORT",
                extra=f"ORDER BY: {', '.join(parse_result.order_by_columns)}",
            )
            children.append(order_node)

        if parse_result.limit_value:
            limit_node = PlanNode(
                "LIMIT",
                rows=parse_result.limit_value,
                extra=f"LIMIT {parse_result.limit_value}",
            )
            children.append(limit_node)

        root = PlanNode(
            "JOIN_QUERY",
            access_type="NESTED_LOOP",
            extra=f"{len(tables)}表关联",
            children=children,
        )

        return root

    def _determine_access(self, table, parse_result, schema_info):
        access_type = "ALL"
        key = None
        rows = 10000
        extra = ""

        where_cols = parse_result.where_columns
        join_cols = []
        for cond in parse_result.join_conditions:
            join_cols.extend(re.findall(r'(\w+\.\w+|\w+)', cond))

        candidate_cols = list(set(where_cols + join_cols + parse_result.order_by_columns))

        if schema_info:
            for tbl in schema_info.get("tables", []):
                if tbl.get("name") == table:
                    estimated_rows = self._estimate_rows(tbl)
                    rows = estimated_rows

                    for idx in tbl.get("indexes", []):
                        idx_cols = idx.get("columns", [])
                        for col in candidate_cols:
                            col_simple = col.split(".")[-1] if "." in col else col
                            if col_simple in idx_cols:
                                if idx.get("type") == "PRIMARY":
                                    access_type = "const"
                                    key = f"PRIMARY"
                                    rows = 1
                                    extra = "主键查找"
                                elif idx.get("type") == "UNIQUE":
                                    access_type = "eq_ref"
                                    key = idx.get("name", "unknown")
                                    rows = 1
                                    extra = "唯一索引查找"
                                else:
                                    if access_type not in ("const", "eq_ref"):
                                        access_type = "ref"
                                        key = idx.get("name", "unknown")
                                        rows = max(1, estimated_rows // 100)
                                        extra = "索引查找"
                                break
                    break
        else:
            if candidate_cols:
                access_type = "ref (假设)"
                key = "unknown_index (需提供表结构确认)"
                rows = 100
                extra = "假设存在索引，请提供表结构确认"
            else:
                access_type = "ALL"
                rows = 10000
                extra = "全表扫描"

        return access_type, key, rows, extra

    def _estimate_rows(self, table_info):
        col_count = len(table_info.get("columns", []))
        base = 1000
        for col in table_info.get("columns", []):
            col_type = col.get("type", "").upper() if isinstance(col, dict) else ""
            if "TEXT" in col_type or "BLOB" in col_type:
                base *= 0.5
            elif "INT" in col_type:
                base *= 1.2
        return int(base)

    def _summarize_where(self, where_clause):
        summary = where_clause
        if len(summary) > 80:
            summary = summary[:77] + "..."
        return summary


def has_aggregate_check(sql):
    return bool(re.search(
        r'\b(SUM|AVG|COUNT|MIN|MAX|GROUP_CONCAT)\s*\(',
        sql,
        re.IGNORECASE,
    ))
