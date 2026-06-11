import re
import sqlparse
from .parser import SQLParser


class PlanNode:
    def __init__(self, node_type, table=None, alias=None, access_type="ALL", key=None,
                 rows=None, extra=None, children=None, filtered=None, possible_keys=None,
                 index_hit_reason=None):
        self.node_type = node_type
        self.table = table
        self.alias = alias
        self.access_type = access_type
        self.key = key
        self.rows = rows
        self.extra = extra or ""
        self.children = children or []
        self.filtered = filtered
        self.possible_keys = possible_keys
        self.index_hit_reason = index_hit_reason

    def to_dict(self):
        result = {
            "id": None,
            "select_type": "SIMPLE",
            "type": self.access_type,
            "table": self.table or "",
            "partitions": None,
            "possible_keys": self.possible_keys,
            "key": self.key,
            "key_len": None,
            "ref": None,
            "rows": self.rows,
            "filtered": self.filtered,
            "Extra": self.extra,
            "node_type": self.node_type,
            "index_hit_reason": self.index_hit_reason,
        }
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        return result

    def to_text(self, indent=0, with_border=True, skip_self=False):
        if skip_self:
            lines = []
            if with_border:
                lines.append("+----+--------------+----------+-----+-----------------------+-------------------------+")
                lines.append("| id | select_type  | table    | type| key                   | Extra                   |")
                lines.append("+----+--------------+----------+-----+-----------------------+-------------------------+")

            for i, child in enumerate(self.children, start=1):
                lines.append(child._to_explain_row(child, i, indent=0))
            lines.append("+----+--------------+----------+-----+-----------------------+-------------------------+")
            return "\n".join(lines)

        prefix = "  " * indent
        arrow = "-> " if indent > 0 else ""

        if with_border and indent == 0:
            lines = []
            lines.append("+----+--------------+----------+-----+-----------------------+-------------------------+")
            lines.append("| id | select_type  | table    | type| key                   | Extra                   |")
            lines.append("+----+--------------+----------+-----+-----------------------+-------------------------+")

            table_str = (self.table or "NULL")[:10]
            type_str = self.access_type[:10]
            key_str = (self.key or "NULL")[:20]
            extra_str = self.extra[:20]
            lines.append(
                f"|  1 | {self._get_select_type():12} | {table_str:10} | {type_str:5} | {key_str:20} | {extra_str:25} |"
            )
            for i, child in enumerate(self.children, start=2):
                lines.append(self._to_explain_row(child, i, indent=0))
            lines.append("+----+--------------+----------+-----+-----------------------+-------------------------+")
            return "\n".join(lines)
        else:
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
            if self.filtered is not None:
                parts.append(f" filtered≈{self.filtered}%")
            if self.extra:
                parts.append(f" ({self.extra})")
            line = "".join(parts)
            child_lines = [c.to_text(indent + 1, with_border=False) for c in self.children]
            return "\n".join([line] + child_lines)

    def _get_select_type(self):
        if self.node_type in ("DRIVING_TABLE", "JOINED_TABLE", "TABLE_SCAN"):
            return "SIMPLE"
        if self.node_type in ("SUBQUERY_MATERIALIZATION",):
            return "SUBQUERY"
        if self.node_type in ("UNION_RESULT",):
            return "UNION RESULT"
        return "SIMPLE"

    def _to_explain_row(self, node, node_id, indent=0):
        table_str = (node.table or "NULL")[:10]
        type_str = node.access_type[:10]
        key_str = (node.key or "NULL")[:20]
        extra_str = node.extra[:20]
        return (
            f"| {node_id:2} | {node._get_select_type():12} | {table_str:10} | {type_str:5} | {key_str:20} | {extra_str:25} |"
        )


class ExecutionPlanGenerator:
    def __init__(self):
        self.parser = SQLParser()
        self.access_type_priority = {
            "system": 0, "const": 1, "eq_ref": 2, "ref": 3,
            "fulltext": 4, "ref_or_null": 5, "index_merge": 6,
            "unique_subquery": 7, "index_subquery": 8,
            "range": 9, "index": 10, "ALL": 11,
        }

    def generate(self, sql: str, schema_info=None):
        parse_result = self.parser.parse(sql)

        if parse_result.statement_type != "SELECT":
            return {
                "plan_text": f"不支持的语句类型: {parse_result.statement_type}",
                "plan_tree": None,
                "explain_output": None,
            }

        root = self._build_plan(parse_result, schema_info)

        steps = []
        for i, child in enumerate(root.children):
            step = {
                "node_type": child.node_type,
                "table": child.table,
                "alias": child.alias,
                "access_type": child.access_type,
                "key": child.key,
                "rows": child.rows,
                "filtered": child.filtered,
                "extra": child.extra,
                "possible_keys": child.possible_keys,
                "index_hit_reason": child.index_hit_reason,
                "is_estimated": schema_info is None,
                "join_order_reason": None,
                "join_condition": None,
            }

            if child.node_type == "DRIVING_TABLE":
                step["join_order_reason"] = "作为驱动表优先扫描，WHERE过滤条件最多或表行数最少"
                if child.index_hit_reason:
                    step["join_order_reason"] += f"；{child.index_hit_reason}"
            elif child.node_type == "JOINED_TABLE":
                step["join_order_reason"] = "作为被驱动表，通过JOIN条件与驱动表关联"
                join_idx = i - 1
                if join_idx < len(parse_result.join_conditions):
                    step["join_condition"] = parse_result.join_conditions[join_idx]
            elif child.node_type == "SUBQUERY_MATERIALIZATION":
                step["join_order_reason"] = "IN/NOT IN子查询物化为临时表"
            elif child.node_type == "SORT":
                step["join_order_reason"] = "结果集排序"
                if "filesort" in (child.extra or "").lower():
                    step["join_order_reason"] += "，需额外排序操作(Using filesort)"
            elif child.node_type == "AGGREGATE":
                step["join_order_reason"] = "聚合计算"
                if "temporary" in (child.extra or "").lower():
                    step["join_order_reason"] += "，需创建临时表(Using temporary)"

            steps.append(step)

        return {
            "plan_text": root.to_text(with_border=False, skip_self=True),
            "explain_output": root.to_text(with_border=True, skip_self=True),
            "plan_tree": root.to_dict(),
            "has_schema": schema_info is not None,
            "steps": steps,
        }

    def _build_plan(self, parse_result, schema_info):
        has_group = bool(parse_result.group_by_columns)
        has_aggregate = bool(parse_result.aggregate_functions)
        has_order = bool(parse_result.order_by_columns)
        has_limit = parse_result.limit_value is not None
        has_distinct = parse_result.distinct
        has_subq = bool(parse_result.subqueries)
        has_in_subq = bool(parse_result.in_subqueries)
        has_union = parse_result.union_count > 0

        tables = parse_result.tables
        if not tables:
            return PlanNode("SIMPLE_QUERY", access_type="NONE", extra="无表查询")

        join_nodes = []
        total_rows = 0

        ordered_tables = self._order_tables(parse_result, schema_info)

        for i, table in enumerate(ordered_tables):
            alias = None
            for a, t in parse_result.table_aliases.items():
                if t == table:
                    alias = a
                    break

            access_type, key, rows, extra, possible_keys, filtered, index_hit_reason = self._determine_access(
                table, parse_result, schema_info
            )
            total_rows += rows

            if i == 0:
                node_type = "DRIVING_TABLE"
                where_match_count = sum(
                    1 for c in parse_result.where_columns
                    if c.split(".")[-1] in [col.get("name", "") for col in self._get_table_columns(table, schema_info)]
                )
                if where_match_count > 0:
                    extra = f"驱动表(WHERE过滤列×{where_match_count}), {extra}"
                else:
                    extra = f"驱动表, {extra}"
            else:
                node_type = "JOINED_TABLE"
                join_type = (
                    parse_result.join_types[i - 1]
                    if i - 1 < len(parse_result.join_types)
                    else "INNER JOIN"
                )
                cond = (
                    parse_result.join_conditions[i - 1]
                    if i - 1 < len(parse_result.join_conditions)
                    else "N/A"
                )
                extra = f"{join_type} ON {cond}, {extra}".strip()

            join_nodes.append(
                PlanNode(
                    node_type,
                    table=table,
                    alias=alias,
                    access_type=access_type,
                    key=key,
                    rows=rows,
                    extra=extra,
                    possible_keys=possible_keys,
                    filtered=filtered,
                    index_hit_reason=index_hit_reason,
                )
            )

        children = list(join_nodes)

        if has_in_subq:
            for in_subq in parse_result.in_subqueries:
                children.append(
                    PlanNode(
                        "SUBQUERY_MATERIALIZATION",
                        table=in_subq.get("subquery_table", "subquery"),
                        access_type="ALL",
                        extra=f"IN/NOT IN 子查询物化临时表",
                        rows=1000,
                    )
                )

        if has_subq and not has_in_subq:
            for sq in parse_result.subquery_infos:
                children.append(
                    PlanNode(
                        "SUBQUERY",
                        table="subquery",
                        access_type="DEPENDENT SUBQUERY",
                        extra=f"位置: {sq.position}，可能需临时表",
                        rows=500,
                    )
                )

        if has_group or has_aggregate:
            extra_parts = []
            if has_group:
                extra_parts.append(f"GROUP BY: {', '.join(parse_result.group_by_columns)}")
            if has_aggregate:
                extra_parts.append(f"聚合函数: {', '.join(parse_result.aggregate_functions)}")

            using_temporary = has_group or has_distinct
            using_filesort = has_order and not self._order_by_uses_index(parse_result, schema_info)

            agg_extra = "Using temporary" if using_temporary else ""
            if using_filesort:
                agg_extra += "; Using filesort" if agg_extra else "Using filesort"

            children.append(
                PlanNode(
                    "AGGREGATE",
                    access_type="ALL",
                    extra="; ".join(extra_parts),
                    rows=max(1, total_rows // 10),
                )
            )

        if has_order:
            filesort = not self._order_by_uses_index(parse_result, schema_info)
            children.append(
                PlanNode(
                    "SORT",
                    access_type="ALL",
                    extra=(
                        f"ORDER BY: {', '.join(parse_result.order_by_columns)} "
                        f"{'(Using filesort)' if filesort else '(Using index)'}"
                    ),
                    rows=total_rows,
                )
            )

        if has_distinct and not has_group:
            children.append(
                PlanNode(
                    "DISTINCT",
                    access_type="ALL",
                    extra="去重排序: Using temporary; Using filesort",
                    rows=max(1, total_rows // 2),
                )
            )

        if has_limit:
            children.append(
                PlanNode(
                    "LIMIT",
                    access_type="ALL",
                    extra=f"LIMIT {parse_result.limit_value}",
                    rows=parse_result.limit_value,
                )
            )

        if has_union:
            children.append(
                PlanNode(
                    "UNION_RESULT",
                    access_type="ALL",
                    extra=f"UNION 去重: Using temporary",
                    rows=total_rows,
                )
            )

        if parse_result.where_clause:
            children.append(
                PlanNode(
                    "FILTER",
                    access_type="ALL",
                    extra=f"WHERE条件过滤: {self._summarize_where(parse_result.where_clause)}",
                )
            )

        join_algo = self._determine_join_algorithm(parse_result, schema_info)
        root = PlanNode(
            "JOIN_QUERY",
            access_type=join_algo,
            extra=f"{len(tables)}表关联，{join_algo}算法",
            children=children,
        )

        return root

    def _order_tables(self, parse_result, schema_info):
        tables = list(parse_result.tables)
        if not schema_info:
            return tables

        table_stats = {}
        for table in tables:
            for tbl in schema_info.get("tables", []):
                if tbl.get("name") == table:
                    rows = self._estimate_rows(tbl)
                    has_pk = bool(tbl.get("primary_key"))
                    table_stats[table] = {"rows": rows, "has_pk": has_pk}
                    break
            if table not in table_stats:
                table_stats[table] = {"rows": 10000, "has_pk": False}

        def sort_key(t):
            stats = table_stats.get(t, {"rows": 10000, "has_pk": False})
            where_count = 0
            for c in parse_result.where_columns:
                col_name = c.split(".")[-1] if "." in c else c
                if col_name in [col.get("name", "") for col in self._get_table_columns(t, schema_info)]:
                    where_count += 1
            return (-where_count, stats["rows"])

        return sorted(tables, key=sort_key)

    def _get_table_columns(self, table_name, schema_info):
        if not schema_info:
            return []
        for tbl in schema_info.get("tables", []):
            if tbl.get("name") == table_name:
                return tbl.get("columns", [])
        return []

    def _determine_access(self, table, parse_result, schema_info):
        access_type = "ALL"
        key = None
        rows = 10000
        extra = ""
        possible_keys = None
        filtered = 100.0
        index_hit_reason = None

        where_cols = parse_result.where_columns
        join_cols = []
        for cond in parse_result.join_conditions:
            join_cols.extend(re.findall(r"(\w+\.\w+|\w+)", cond))

        candidate_cols = list(set(where_cols + join_cols + parse_result.order_by_columns))

        if schema_info:
            table_info = None
            for tbl in schema_info.get("tables", []):
                if tbl.get("name") == table:
                    table_info = tbl
                    break

            if table_info:
                rows = self._estimate_rows(table_info)
                estimated_rows = rows

                possible_keys_list = []

                best_index = None
                best_access_type = "ALL"
                best_match_count = 0

                for idx in table_info.get("indexes", []):
                    idx_cols = idx.get("columns", [])
                    idx_type = idx.get("type", "INDEX")

                    match_count = 0
                    for col in candidate_cols:
                        col_simple = col.split(".")[-1] if "." in col else col
                        if col_simple in idx_cols:
                            match_count += 1

                    if match_count > 0:
                        possible_keys_list.append(idx.get("name", "unknown"))

                    if idx_cols and idx_cols[0] in [c.split(".")[-1] for c in candidate_cols]:
                        if idx_type == "PRIMARY":
                            if best_access_type != "const":
                                best_access_type = "const"
                                best_index = idx
                                best_match_count = match_count
                        elif idx_type == "UNIQUE":
                            if best_access_type not in ("const", "eq_ref"):
                                best_access_type = "eq_ref"
                                best_index = idx
                                best_match_count = match_count
                        else:
                            if best_access_type in ("ALL", "range"):
                                if match_count >= 2 and len(idx_cols) >= 2:
                                    best_access_type = "ref"
                                elif match_count == 1:
                                    best_access_type = "ref"
                                best_index = idx
                                best_match_count = match_count

                if best_index:
                    access_type = best_access_type
                    key = best_index.get("name")

                    if access_type == "const":
                        rows = 1
                        extra = "主键唯一索引，单行查找"
                        index_hit_reason = f"主键等值查询，命中索引{key}"
                        filtered = 100.0
                    elif access_type == "eq_ref":
                        rows = 1
                        extra = "唯一索引关联"
                        index_hit_reason = f"唯一索引等值关联，命中索引{key}"
                        filtered = 100.0
                    elif access_type == "ref":
                        rows = max(1, estimated_rows // 100)
                        matched_where_col = self._find_matched_where_col(candidate_cols, best_index)
                        if matched_where_col:
                            index_hit_reason = f"WHERE {matched_where_col} 匹配索引{key}首列"
                        else:
                            index_hit_reason = f"等值条件匹配索引{key}"
                        extra = f"索引{key}查找"
                        filtered = 10.0
                    elif access_type == "range":
                        rows = max(1, estimated_rows // 10)
                        extra = f"索引{key}范围扫描"
                        index_hit_reason = f"范围条件使用索引{key}"
                        filtered = 33.3

                possible_keys = possible_keys_list if possible_keys_list else None

                if access_type == "ALL":
                    extra = "全表扫描"
                    if not possible_keys:
                        extra += "，无可用索引"
                    else:
                        extra += "，索引选择性太低"
                    filtered = 100.0
        else:
            if candidate_cols:
                access_type = "ALL (假设)"
                key = "unknown_index (需提供表结构确认)"
                rows = 10000
                extra = "未提供表结构，假设有待验证"
                possible_keys = ["(需表结构确认)"]
                filtered = None
            else:
                access_type = "ALL"
                rows = 10000
                extra = "全表扫描，无过滤条件"
                filtered = 100.0

        return access_type, key, rows, extra, possible_keys, filtered, index_hit_reason

    def _find_matched_where_col(self, candidate_cols, best_index):
        idx_first_col = best_index.get("columns", [""])[0] if best_index.get("columns") else ""
        for col in candidate_cols:
            col_simple = col.split(".")[-1] if "." in col else col
            if col_simple == idx_first_col:
                return col
        return None

    def _estimate_rows(self, table_info):
        col_count = len(table_info.get("columns", []))
        base = 10000

        for col in table_info.get("columns", []):
            col_type = col.get("type", "").upper() if isinstance(col, dict) else ""
            if "TEXT" in col_type or "BLOB" in col_type:
                base *= 0.8
            elif "INT" in col_type:
                base *= 1.1
            elif "DATE" in col_type or "DATETIME" in col_type:
                base *= 1.05

        if table_info.get("primary_key"):
            base *= 1.2

        return int(max(100, base))

    def _order_by_uses_index(self, parse_result, schema_info):
        if not schema_info or not parse_result.order_by_columns:
            return False

        order_cols = [c.split(".")[-1] for c in parse_result.order_by_columns]
        main_table = parse_result.tables[0] if parse_result.tables else None

        for tbl in schema_info.get("tables", []):
            if main_table and tbl.get("name") != main_table:
                continue
            for idx in tbl.get("indexes", []):
                idx_cols = idx.get("columns", [])
                if len(idx_cols) >= len(order_cols):
                    match = all(
                        order_cols[i] == idx_cols[i]
                        for i in range(len(order_cols))
                    )
                    if match:
                        return True
        return False

    def _determine_join_algorithm(self, parse_result, schema_info):
        if len(parse_result.tables) <= 1:
            return "SINGLE_TABLE"

        has_indexes = True
        if schema_info:
            for cond in parse_result.join_conditions:
                cols = re.findall(r"(\w+\.\w+|\w+)", cond)
                for col in cols:
                    has_idx = False
                    for tbl in schema_info.get("tables", []):
                        for idx in tbl.get("indexes", []):
                            if col.split(".")[-1] in idx.get("columns", []):
                                has_idx = True
                                break
                        if has_idx:
                            break
                    if not has_idx:
                        has_indexes = False
                        break
        else:
            has_indexes = False

        if has_indexes:
            return "nested_loop"
        else:
            return "block_nested_loop"

    def _summarize_where(self, where_clause):
        summary = where_clause.replace("WHERE", "", 1).strip()
        if len(summary) > 60:
            summary = summary[:57] + "..."
        return summary
