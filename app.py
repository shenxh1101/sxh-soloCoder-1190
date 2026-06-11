from flask import Flask, request, jsonify
from sql_optimizer.rules import RuleEngine
from sql_optimizer.schema import SchemaParser
from sql_optimizer.report import ReportGenerator
from sql_optimizer.execution_plan import ExecutionPlanGenerator
from sql_optimizer.highlight import SQLHighlighter

app = Flask(__name__)

rule_engine = RuleEngine()
schema_parser = SchemaParser()
report_generator = ReportGenerator()
plan_generator = ExecutionPlanGenerator()
highlighter = SQLHighlighter()


@app.route("/api/v1/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "service": "sql-optimizer"})


@app.route("/api/v1/analyze", methods=["POST"])
def analyze_sql():
    data = request.get_json(silent=True)
    if not data or "sql" not in data:
        return jsonify({"error": "请求体中必须包含 'sql' 字段"}), 400

    sql = data["sql"]
    schema_input = data.get("schema")

    schema_info = None
    if schema_input:
        try:
            schema_info = schema_parser.parse(schema_input)
        except Exception as e:
            return jsonify({"error": f"表结构解析失败: {str(e)}"}), 400

    try:
        result = rule_engine.analyze(sql, schema_info)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"分析失败: {str(e)}"}), 500


@app.route("/api/v1/analyze/batch", methods=["POST"])
def analyze_batch():
    data = request.get_json(silent=True)
    if not data or "queries" not in data:
        return jsonify({"error": "请求体中必须包含 'queries' 字段(数组)"}), 400

    queries = data["queries"]
    if not isinstance(queries, list) or len(queries) == 0:
        return jsonify({"error": "'queries' 必须是非空数组"}), 400

    schema_input = data.get("schema")

    schema_info = None
    if schema_input:
        try:
            schema_info = schema_parser.parse(schema_input)
        except Exception as e:
            return jsonify({"error": f"表结构解析失败: {str(e)}"}), 400

    results = []
    errors = []
    for i, sql in enumerate(queries):
        try:
            result = rule_engine.analyze(sql, schema_info)
            results.append(result)
        except Exception as e:
            errors.append({"index": i, "sql": sql, "error": str(e)})
            results.append({
                "sql": sql,
                "error": str(e),
                "suggestions": [],
            })

    response = {"results": results}
    if errors:
        response["errors"] = errors

    return jsonify(response)


@app.route("/api/v1/report", methods=["POST"])
def generate_report():
    data = request.get_json(silent=True)
    if not data or "queries" not in data:
        return jsonify({"error": "请求体中必须包含 'queries' 字段(数组)"}), 400

    queries = data["queries"]
    if not isinstance(queries, list) or len(queries) == 0:
        return jsonify({"error": "'queries' 必须是非空数组"}), 400

    schema_input = data.get("schema")

    try:
        report = report_generator.generate(queries, schema_input)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": f"报告生成失败: {str(e)}"}), 500


@app.route("/api/v1/execution-plan", methods=["POST"])
def generate_execution_plan():
    data = request.get_json(silent=True)
    if not data or "sql" not in data:
        return jsonify({"error": "请求体中必须包含 'sql' 字段"}), 400

    sql = data["sql"]
    schema_input = data.get("schema")

    schema_info = None
    if schema_input:
        try:
            schema_info = schema_parser.parse(schema_input)
        except Exception as e:
            return jsonify({"error": f"表结构解析失败: {str(e)}"}), 400

    try:
        result = plan_generator.generate(sql, schema_info)
        return jsonify({"sql": sql, "execution_plan": result})
    except Exception as e:
        return jsonify({"error": f"执行计划生成失败: {str(e)}"}), 500


@app.route("/api/v1/compare", methods=["POST"])
def compare_sql():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "请求体不能为空"}), 400

    original = data.get("original_sql")
    optimized = data.get("optimized_sql")

    if original and optimized:
        try:
            result = highlighter.compare(original, optimized)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": f"对比失败: {str(e)}"}), 500

    sql = data.get("sql")
    if not sql:
        return jsonify({"error": "请提供 'original_sql'+'optimized_sql' 或 'sql' 字段"}), 400

    schema_input = data.get("schema")
    schema_info = None
    if schema_input:
        try:
            schema_info = schema_parser.parse(schema_input)
        except Exception as e:
            return jsonify({"error": f"表结构解析失败: {str(e)}"}), 400

    try:
        analysis = rule_engine.analyze(sql, schema_info)
        optimized_sql = analysis.get("optimized_sql", sql)
        result = highlighter.compare(sql, optimized_sql)
        result["suggestions"] = analysis.get("suggestions", [])
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"对比失败: {str(e)}"}), 500


@app.route("/api/v1/highlight", methods=["POST"])
def highlight_sql():
    data = request.get_json(silent=True)
    if not data or "sql" not in data:
        return jsonify({"error": "请求体中必须包含 'sql' 字段"}), 400

    sql = data["sql"]
    try:
        highlighted = highlighter.highlight(sql)
        return jsonify({"sql": sql, "highlighted": highlighted, "css": highlighter._get_css()})
    except Exception as e:
        return jsonify({"error": f"高亮失败: {str(e)}"}), 500


@app.route("/api/v1/schema/parse", methods=["POST"])
def parse_schema():
    data = request.get_json(silent=True)
    if not data or "schema" not in data:
        return jsonify({"error": "请求体中必须包含 'schema' 字段(DDL或JSON)"}), 400

    schema_input = data["schema"]
    try:
        result = schema_parser.parse(schema_input)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"表结构解析失败: {str(e)}"}), 400


@app.route("/api/v1/rules", methods=["GET"])
def list_rules():
    rules = [
        {"rule_id": "R001", "name": "避免使用 SELECT *", "severity": "high",
         "description": "SELECT * 会导致不必要的数据传输和I/O开销"},
        {"rule_id": "R002", "name": "避免在WHERE子句中对字段使用函数", "severity": "high",
         "description": "在WHERE子句中对列使用函数会导致索引失效"},
        {"rule_id": "R003", "name": "将子查询改写为JOIN", "severity": "medium",
         "description": "子查询可能产生临时表，JOIN通常有更好的执行计划"},
        {"rule_id": "R004", "name": "使用索引列进行关联", "severity": "medium",
         "description": "未索引的关联列会导致嵌套循环全表扫描"},
        {"rule_id": "R005", "name": "为WHERE过滤列创建索引", "severity": "medium",
         "description": "为频繁过滤的列创建索引可以大幅提升查询性能"},
        {"rule_id": "R006", "name": "为ORDER BY列创建索引", "severity": "low",
         "description": "为排序列创建索引以避免额外的排序操作"},
        {"rule_id": "R007", "name": "避免LIKE前缀通配符", "severity": "high",
         "description": "LIKE '%xxx' 模式会导致索引失效"},
        {"rule_id": "R008", "name": "为无WHERE的全表查询添加LIMIT", "severity": "medium",
         "description": "无限制的全表查询可能返回大量数据"},
        {"rule_id": "R009", "name": "谨慎使用DISTINCT", "severity": "low",
         "description": "DISTINCT会导致排序去重，开销较大"},
        {"rule_id": "R010", "name": "考虑将多个OR条件改写为UNION ALL", "severity": "medium",
         "description": "多个OR条件可能无法有效利用索引"},
        {"rule_id": "R011", "name": "避免使用NOT IN", "severity": "medium",
         "description": "NOT IN 性能不如 LEFT JOIN 或 NOT EXISTS"},
        {"rule_id": "R012", "name": "避免隐式类型转换", "severity": "medium",
         "description": "隐式类型转换会导致索引失效"},
    ]
    return jsonify({"rules": rules, "total": len(rules)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
