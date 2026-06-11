from datetime import datetime
from .rules import RuleEngine
from .schema import SchemaParser
from .execution_plan import ExecutionPlanGenerator
from .highlight import SQLHighlighter


ISSUE_CATEGORIES = {
    "R001": {"category": "性能优化", "subcategory": "SELECT写法"},
    "R002": {"category": "性能优化", "subcategory": "索引失效"},
    "R003": {"category": "SQL改写", "subcategory": "子查询优化"},
    "R004": {"category": "索引建议", "subcategory": "JOIN关联"},
    "R005": {"category": "索引建议", "subcategory": "WHERE过滤"},
    "R006": {"category": "索引建议", "subcategory": "ORDER BY排序"},
    "R007": {"category": "性能优化", "subcategory": "索引失效"},
    "R008": {"category": "SQL改写", "subcategory": "分页限制"},
    "R009": {"category": "SQL改写", "subcategory": "去重优化"},
    "R010": {"category": "SQL改写", "subcategory": "OR条件优化"},
    "R011": {"category": "SQL改写", "subcategory": "子查询优化"},
    "R012": {"category": "性能优化", "subcategory": "隐式转换"},
    "R013": {"category": "SQL改写", "subcategory": "HAVING用法"},
}


class ReportGenerator:
    def __init__(self):
        self.rule_engine = RuleEngine()
        self.schema_parser = SchemaParser()
        self.plan_generator = ExecutionPlanGenerator()
        self.highlighter = SQLHighlighter()

    def generate(self, sql_list, schema_input=None):
        schema_info = None
        if schema_input:
            schema_info = self.schema_parser.parse(schema_input)

        results = []
        all_suggestions_flat = []
        global_issue_types = {}
        global_issue_categories = {}
        global_in_subquery_count = 0
        global_function_on_column_count = 0
        global_select_star_count = 0
        global_missing_limit_count = 0

        for sql in sql_list:
            analysis = self.rule_engine.analyze(sql, schema_info)
            execution_plan = self.plan_generator.generate(sql, schema_info)
            comparison = self.highlighter.compare(
                sql, analysis.get("optimized_sql", sql)
            )

            suggestions = analysis.get("suggestions", [])
            parse_result = analysis.get("parse_result", {})

            for s in suggestions:
                rule_id = s.get("rule_id", "")
                cat = ISSUE_CATEGORIES.get(rule_id, {"category": "其他", "subcategory": "未分类"})
                s["category"] = cat["category"]
                s["subcategory"] = cat["subcategory"]
                all_suggestions_flat.append(s)

                if rule_id not in global_issue_types:
                    global_issue_types[rule_id] = 0
                global_issue_types[rule_id] += 1

                if cat["category"] not in global_issue_categories:
                    global_issue_categories[cat["category"]] = 0
                global_issue_categories[cat["category"]] += 1

            if parse_result.get("in_subqueries"):
                global_in_subquery_count += len(parse_result.get("in_subqueries"))

            if parse_result.get("where_functions"):
                global_function_on_column_count += 1

            if parse_result.get("has_star"):
                global_select_star_count += 1

            if parse_result.get("limit_value") is None and not parse_result.get("where_clause"):
                global_missing_limit_count += 1

            result = {
                "original_sql": sql,
                "optimized_sql": analysis.get("optimized_sql", sql),
                "statement_type": analysis.get("statement_type"),
                "suggestions": suggestions,
                "suggestions_count": len(suggestions),
                "execution_plan": execution_plan,
                "comparison": comparison,
                "parse_result": parse_result,
                "summary": analysis.get("summary"),
            }

            high_count = sum(1 for s in suggestions if s.get("severity") == "high")
            medium_count = sum(1 for s in suggestions if s.get("severity") == "medium")
            low_count = sum(1 for s in suggestions if s.get("severity") == "low")
            result["severity_summary"] = {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            }

            result["query_issues"] = self._classify_query_issues(
                suggestions, parse_result
            )

            results.append(result)

        total_high = sum(r["severity_summary"]["high"] for r in results)
        total_medium = sum(r["severity_summary"]["medium"] for r in results)
        total_low = sum(r["severity_summary"]["low"] for r in results)

        if total_high > 0:
            overall_score = max(0, 100 - total_high * 20 - total_medium * 10 - total_low * 5)
        elif total_medium > 0:
            overall_score = max(50, 100 - total_medium * 10 - total_low * 5)
        elif total_low > 0:
            overall_score = max(70, 100 - total_low * 5)
        else:
            overall_score = 100

        report = {
            "generated_at": datetime.now().isoformat(),
            "total_queries": len(sql_list),
            "overall_score": overall_score,
            "score_grade": self._get_score_grade(overall_score),
            "overview": self._build_diagnostic_overview(all_suggestions_flat, results),
            "total_suggestions": sum(r["suggestions_count"] for r in results),
            "global_severity_summary": {
                "high": total_high,
                "medium": total_medium,
                "low": total_low,
            },
            "global_issue_types": global_issue_types,
            "global_issue_categories": global_issue_categories,
            "global_antipatterns": {
                "in_subquery_count": global_in_subquery_count,
                "function_on_column_count": global_function_on_column_count,
                "select_star_count": global_select_star_count,
                "missing_limit_count": global_missing_limit_count,
            },
            "schema_provided": schema_info is not None,
            "schema_info": schema_info,
            "prioritized_actions": self._generate_prioritized_actions(
                all_suggestions_flat, schema_info
            ),
            "results": results,
        }

        return report

    def _classify_query_issues(self, suggestions, parse_result):
        issues = {
            "performance": [],
            "index": [],
            "rewrite": [],
            "style": [],
        }

        for s in suggestions:
            rid = s.get("rule_id", "")
            if rid in ("R001", "R002", "R007", "R010", "R011", "R012"):
                issues["performance"].append(rid)
            elif rid in ("R004", "R005", "R006"):
                issues["index"].append(rid)
            elif rid in ("R003", "R008", "R013"):
                issues["rewrite"].append(rid)
            elif rid in ("R009",):
                issues["style"].append(rid)

        if parse_result.get("in_subqueries"):
            issues["in_subqueries"] = parse_result.get("in_subqueries")

        return issues

    def _get_score_grade(self, score):
        if score >= 90:
            return {"grade": "A", "label": "优秀", "color": "#28a745"}
        elif score >= 80:
            return {"grade": "B", "label": "良好", "color": "#17a2b8"}
        elif score >= 70:
            return {"grade": "C", "label": "一般", "color": "#ffc107"}
        elif score >= 60:
            return {"grade": "D", "label": "较差", "color": "#fd7e14"}
        else:
            return {"grade": "E", "label": "严重", "color": "#dc3545"}

    def _count_issue_points(self, suggestion):
        details = suggestion.get("details", {})
        if "functions" in details and isinstance(details["functions"], list):
            return len(details["functions"])
        if "affected_columns" in details and isinstance(details["affected_columns"], list):
            return len(details["affected_columns"])
        return 1

    def _build_diagnostic_overview(self, all_suggestions_flat, results):
        issue_groups = {}
        for s in all_suggestions_flat:
            rid = s.get("rule_id", "")
            if rid == "ERR":
                continue
            if rid not in issue_groups:
                issue_groups[rid] = {
                    "rule_id": rid,
                    "title": s.get("title", ""),
                    "severity": s.get("severity", ""),
                    "category": s.get("category", ""),
                    "subcategory": s.get("subcategory", ""),
                    "affected_sql_indices": [],
                    "affected_sql_summaries": [],
                    "occurrences_per_sql": {},
                    "occurrences": 0,
                    "details_samples": [],
                }
            issue_groups[rid]["occurrences"] += self._count_issue_points(s)
            if len(issue_groups[rid]["details_samples"]) < 2:
                issue_groups[rid]["details_samples"].append({
                    "description": s.get("description", ""),
                    "suggested": s.get("suggested", ""),
                })

        for idx, r in enumerate(results):
            rid_counter = {}
            for s in r.get("suggestions", []):
                rid = s.get("rule_id", "")
                if rid in issue_groups:
                    rid_counter[rid] = rid_counter.get(rid, 0) + self._count_issue_points(s)
            for rid, count_in_sql in rid_counter.items():
                issue_groups[rid]["affected_sql_indices"].append(idx)
                issue_groups[rid]["occurrences_per_sql"][idx] = count_in_sql
                sql_short = r["original_sql"].strip().replace("\n", " ")
                if len(sql_short) > 80:
                    sql_short = sql_short[:77] + "..."
                suffix = f" [该类问题×{count_in_sql}]" if count_in_sql > 1 else ""
                issue_groups[rid]["affected_sql_summaries"].append(
                    f"{sql_short}{suffix}"
                )

        severity_priority = {"high": 0, "medium": 1, "low": 2}
        sorted_groups = sorted(
            issue_groups.values(),
            key=lambda g: (severity_priority.get(g["severity"], 3), -len(g["affected_sql_indices"]))
        )

        overview_items = []
        for g in sorted_groups:
            affected_count = len(g["affected_sql_indices"])
            if affected_count >= 5:
                impact = "全局范围"
            elif affected_count >= 2:
                impact = f"影响{affected_count}条SQL"
            else:
                impact = "影响1条SQL"

            if g["occurrences"] > affected_count:
                impact += f"（共{g['occurrences']}个问题点）"

            priority = "P0" if g["severity"] == "high" else ("P1" if g["severity"] == "medium" else "P2")

            item = {
                "rule_id": g["rule_id"],
                "title": g["title"],
                "severity": g["severity"],
                "category": g["category"],
                "subcategory": g["subcategory"],
                "priority": priority,
                "impact_scope": impact,
                "affected_queries": affected_count,
                "total_occurrences": g["occurrences"],
                "affected_sql_summaries": g["affected_sql_summaries"],
                "occurrences_per_sql": g["occurrences_per_sql"],
                "samples": g["details_samples"],
            }

            if g["rule_id"] == "R002":
                item["diagnosis"] = "WHERE子句函数导致索引失效，需将函数移至值侧"
                item["action"] = "改写 WHERE 条件，保持列为裸列"
            elif g["rule_id"] == "R003":
                item["diagnosis"] = "IN/NOT IN 子查询产生临时表，性能较差"
                item["action"] = "改写为 JOIN 或 EXISTS"
            elif g["rule_id"] == "R001":
                item["diagnosis"] = "SELECT * 导致不必要数据传输"
                item["action"] = "展开为具体字段列表"
            elif g["rule_id"] == "R004":
                item["diagnosis"] = "JOIN关联列缺少索引，嵌套循环效率低"
                item["action"] = "为关联列创建索引"
            elif g["rule_id"] == "R005":
                item["diagnosis"] = "WHERE过滤列缺少索引支持"
                item["action"] = "根据查询模式创建单列或联合索引"
            elif g["rule_id"] == "R006":
                item["diagnosis"] = "ORDER BY列缺少索引，需额外排序"
                item["action"] = "为排序列创建索引消除filesort"
            elif g["rule_id"] == "R007":
                item["diagnosis"] = "LIKE前缀通配符导致索引失效"
                item["action"] = "改用前缀匹配或全文索引"
            elif g["rule_id"] == "R008":
                item["diagnosis"] = "无限制查询可能返回海量数据"
                item["action"] = "添加 LIMIT 子句"
            elif g["rule_id"] == "R011":
                item["diagnosis"] = "NOT IN 有NULL陷阱且性能差"
                item["action"] = "改写为 NOT EXISTS 或 LEFT JOIN IS NULL"
            elif g["rule_id"] == "R012":
                item["diagnosis"] = "隐式类型转换导致索引失效"
                item["action"] = "确保比较值类型与列类型一致"
            elif g["rule_id"] == "R013":
                item["diagnosis"] = "HAVING缺少GROUP BY，条件应在WHERE"
                item["action"] = "将非聚合条件移至WHERE"
            else:
                item["diagnosis"] = g["title"]
                item["action"] = g.get("details_samples", [{}])[0].get("suggested", "参考建议详情")

            overview_items.append(item)

        return overview_items

    def _generate_prioritized_actions(self, all_suggestions, schema_info):
        actions = []

        high_severity = [s for s in all_suggestions if s.get("severity") == "high"]
        for s in high_severity:
            action = {
                "priority": "P0",
                "rule_id": s.get("rule_id"),
                "title": s.get("title"),
                "description": s.get("description"),
                "suggested": s.get("suggested"),
                "impact": "可能导致全表扫描或严重性能问题",
            }
            if action not in actions:
                actions.append(action)

        index_suggestions = [s for s in all_suggestions if s.get("rule_id") in ("R004", "R005", "R006")]
        composite_suggestions = [
            s for s in index_suggestions
            if s.get("details", {}).get("is_composite")
        ]
        single_suggestions = [
            s for s in index_suggestions
            if not s.get("details", {}).get("is_composite")
        ]

        for s in composite_suggestions:
            action = {
                "priority": "P1",
                "rule_id": s.get("rule_id"),
                "title": s.get("title"),
                "description": s.get("description"),
                "suggested": s.get("suggested"),
                "impact": "联合索引可大幅提升多条件查询性能",
            }
            if action not in actions:
                actions.append(action)

        for s in single_suggestions:
            action = {
                "priority": "P1",
                "rule_id": s.get("rule_id"),
                "title": s.get("title"),
                "description": s.get("description"),
                "suggested": s.get("suggested"),
                "impact": "单列索引可避免全表扫描",
            }
            if action not in actions:
                actions.append(action)

        medium_severity = [s for s in all_suggestions if s.get("severity") == "medium"]
        for s in medium_severity:
            if s.get("rule_id") not in ("R004", "R005", "R006"):
                action = {
                    "priority": "P2",
                    "rule_id": s.get("rule_id"),
                    "title": s.get("title"),
                    "description": s.get("description"),
                    "suggested": s.get("suggested"),
                    "impact": "中等性能影响，建议优化",
                }
                if action not in actions:
                    actions.append(action)

        low_severity = [s for s in all_suggestions if s.get("severity") == "low"]
        for s in low_severity:
            action = {
                "priority": "P3",
                "rule_id": s.get("rule_id"),
                "title": s.get("title"),
                "description": s.get("description"),
                "suggested": s.get("suggested"),
                "impact": "低优先级，长期优化",
            }
            if action not in actions:
                actions.append(action)

        return actions
