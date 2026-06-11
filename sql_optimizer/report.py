from datetime import datetime
from .rules import RuleEngine
from .schema import SchemaParser
from .execution_plan import ExecutionPlanGenerator
from .highlight import SQLHighlighter


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
        for sql in sql_list:
            analysis = self.rule_engine.analyze(sql, schema_info)
            execution_plan = self.plan_generator.generate(sql, schema_info)
            comparison = self.highlighter.compare(
                sql, analysis.get("optimized_sql", sql)
            )

            result = {
                "original_sql": sql,
                "optimized_sql": analysis.get("optimized_sql", sql),
                "statement_type": analysis.get("statement_type"),
                "suggestions": analysis.get("suggestions", []),
                "suggestions_count": len(analysis.get("suggestions", [])),
                "execution_plan": execution_plan,
                "comparison": comparison,
            }

            high_count = sum(1 for s in analysis.get("suggestions", []) if s.get("severity") == "high")
            medium_count = sum(1 for s in analysis.get("suggestions", []) if s.get("severity") == "medium")
            low_count = sum(1 for s in analysis.get("suggestions", []) if s.get("severity") == "low")
            result["severity_summary"] = {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            }

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
            "total_suggestions": sum(r["suggestions_count"] for r in results),
            "global_severity_summary": {
                "high": total_high,
                "medium": total_medium,
                "low": total_low,
            },
            "schema_provided": schema_info is not None,
            "results": results,
        }

        return report
