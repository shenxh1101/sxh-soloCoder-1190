import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.path.insert(0, ".")

from sql_optimizer.rules import RuleEngine
from sql_optimizer.schema import SchemaParser
from sql_optimizer.execution_plan import ExecutionPlanGenerator
from sql_optimizer.highlight import SQLHighlighter
from sql_optimizer.report import ReportGenerator

engine = RuleEngine()
schema_parser = SchemaParser()
plan_gen = ExecutionPlanGenerator()
highlighter = SQLHighlighter()
report_gen = ReportGenerator()

passed = 0
failed = 0

def run_test(name, test_func):
    global passed, failed
    print(f"\n[测试] {name}")
    try:
        test_func()
        print(f"  ✅ 通过")
        passed += 1
    except AssertionError as e:
        print(f"  ❌ 断言失败: {e}")
        failed += 1
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

print("=" * 80)
print("  SQL 优化建议引擎 - 第三轮增强测试")
print("=" * 80)

# ---- 1. IN子查询改写返回完整可执行SELECT ----

def test_in_subquery_join_complete():
    sql = """
    SELECT o.order_id, o.total
    FROM orders o
    WHERE o.customer_id IN (
        SELECT c.customer_id 
        FROM customers c 
        WHERE c.country = 'USA'
    )
    """
    result = engine.analyze(sql)
    suggestions = result["suggestions"]
    r003 = [s for s in suggestions if s["rule_id"] == "R003"]
    assert len(r003) >= 1, f"应给出 R003 建议"
    
    details = r003[0].get("details", {})
    rewrite_options = details.get("rewrite_options", [])
    assert len(rewrite_options) >= 2, f"应提供 2 种改写方案"
    
    for opt in rewrite_options:
        sql_text = opt["sql"]
        assert "SELECT" in sql_text, f"改写方案应包含SELECT: {sql_text}"
        assert "FROM" in sql_text, f"改写方案应包含FROM: {sql_text}"
        print(f"    {opt['type']}: {sql_text[:120]}...")
    
    join_opt = [o for o in rewrite_options if o["type"] == "JOIN改写"][0]
    assert "INNER JOIN" in join_opt["sql"], f"JOIN改写应包含INNER JOIN: {join_opt['sql']}"
    assert "orders" in join_opt["sql"], f"JOIN改写应包含主表orders: {join_opt['sql']}"
    
    exists_opt = [o for o in rewrite_options if o["type"] == "EXISTS改写"][0]
    assert "EXISTS" in exists_opt["sql"], f"EXISTS改写应包含EXISTS: {exists_opt['sql']}"
    assert "orders" in exists_opt["sql"], f"EXISTS改写应包含主表orders: {exists_opt['sql']}"

run_test("IN子查询改写返回完整可执行SELECT", test_in_subquery_join_complete)

# ---- 2. IN子查询改写保留其他WHERE条件和ORDER BY/LIMIT ----

def test_in_subquery_preserves_other_clauses():
    sql = """
    SELECT o.order_id, o.total
    FROM orders o
    WHERE o.status = 'active' AND o.customer_id IN (
        SELECT c.customer_id 
        FROM customers c 
        WHERE c.country = 'USA'
    )
    ORDER BY o.order_date DESC
    LIMIT 10
    """
    result = engine.analyze(sql)
    suggestions = result["suggestions"]
    r003 = [s for s in suggestions if s["rule_id"] == "R003"]
    assert len(r003) >= 1
    
    details = r003[0].get("details", {})
    rewrite_options = details.get("rewrite_options", [])
    
    for opt in rewrite_options:
        sql_text = opt["sql"]
        assert "status" in sql_text, f"改写应保留其他WHERE条件(status): {sql_text}"
        assert "ORDER BY" in sql_text, f"改写应保留ORDER BY: {sql_text}"
        assert "LIMIT 10" in sql_text, f"改写应保留LIMIT: {sql_text}"
        print(f"    {opt['type']}: {sql_text[:150]}...")

run_test("IN子查询改写保留其他WHERE/ORDER BY/LIMIT", test_in_subquery_preserves_other_clauses)

# ---- 3. 函数诊断精确性 ----

def test_function_diagnosis_precision():
    sql = "SELECT * FROM orders WHERE UPPER(customer_name) = 'JOHN' AND YEAR(order_date) = 2024 AND status = 'active'"
    result = engine.analyze(sql)
    pr = result["parse_result"]
    
    where_cols = pr["where_columns"]
    where_funcs = pr["where_functions"]
    assert "status" in where_cols, f"status 应在WHERE列中: {where_cols}"
    assert "UPPER" not in where_cols, f"UPPER 不应在WHERE列中: {where_cols}"
    
    func_names = [f.get("function", "") for f in where_funcs]
    func_args = [f.get("arguments", "") for f in where_funcs]
    assert "UPPER" in func_names, f"UPPER 应在函数列表中: {func_names}"
    assert "YEAR" in func_names, f"YEAR 应在函数列表中: {func_names}"
    assert any("customer_name" in a for a in func_args), f"customer_name 应在参数中: {func_args}"
    assert any("order_date" in a for a in func_args), f"order_date 应在参数中: {func_args}"
    
    suggestions = result["suggestions"]
    r002 = [s for s in suggestions if s["rule_id"] == "R002"]
    assert len(r002) >= 1, f"应有 R002 建议"
    
    details = r002[0].get("details", {})
    if "functions" in details:
        func_details = details["functions"]
        affected_cols = details.get("affected_columns", [])
        assert "customer_name" in affected_cols, f"affected_columns 应含 customer_name: {affected_cols}"
        assert "order_date" in affected_cols, f"affected_columns 应含 order_date: {affected_cols}"
        assert "status" not in affected_cols, f"status 不应在 affected_columns 中: {affected_cols}"
        for fd in func_details:
            assert "function" in fd, f"每个函数详情应有function字段: {fd}"
            assert "column" in fd, f"每个函数详情应有column字段: {fd}"
            assert "alternative" in fd, f"每个函数详情应有alternative字段: {fd}"
    else:
        assert "column" in details, f"单函数模式应有column字段: {details}"
        assert details["column"] != "status", f"函数字段不应是status: {details}"

run_test("函数诊断精确性", test_function_diagnosis_precision)

# ---- 4. SELECT聚合函数不应报为WHERE函数问题 ----

def test_select_aggregates_not_flagged():
    sql = "SELECT COUNT(*), SUM(total), AVG(amount) FROM orders WHERE status = 'active'"
    result = engine.analyze(sql)
    
    suggestions = result["suggestions"]
    r002 = [s for s in suggestions if s["rule_id"] == "R002"]
    assert len(r002) == 0, f"SELECT聚合函数不应触发 R002: {[s['title'] for s in r002]}"
    
    pr = result["parse_result"]
    where_funcs = pr["where_functions"]
    func_names = [f.get("function", "") for f in where_funcs]
    assert "COUNT" not in func_names, f"COUNT 不应在WHERE函数列表中: {func_names}"
    assert "SUM" not in func_names, f"SUM 不应在WHERE函数列表中: {func_names}"

run_test("SELECT聚合不报为WHERE函数问题", test_select_aggregates_not_flagged)

# ---- 5. 批量报告诊断overview ----

def test_report_diagnostic_overview():
    sql_list = [
        "SELECT * FROM orders WHERE UPPER(name) = 'TEST'",
        "SELECT o.id FROM orders o WHERE o.customer_id IN (SELECT id FROM customers WHERE country = 'USA')",
        "SELECT * FROM customers WHERE country = 'USA' ORDER BY signup_date",
        "SELECT COUNT(*) FROM orders GROUP BY status HAVING COUNT(*) > 100"
    ]
    
    report = report_gen.generate(sql_list)
    
    assert "overview" in report, "报告应包含 overview"
    overview = report["overview"]
    assert len(overview) > 0, "overview 不应为空"
    
    for item in overview:
        assert "rule_id" in item, f"overview项应有rule_id: {item}"
        assert "priority" in item, f"overview项应有priority: {item}"
        assert "impact_scope" in item, f"overview项应有impact_scope: {item}"
        assert "affected_queries" in item, f"overview项应有affected_queries: {item}"
        assert "affected_sql_summaries" in item, f"overview项应有affected_sql_summaries: {item}"
        assert "diagnosis" in item, f"overview项应有diagnosis: {item}"
        assert "action" in item, f"overview项应有action: {item}"
        print(f"    [{item['priority']}] {item['rule_id']}: {item['diagnosis']} (影响{item['affected_queries']}条SQL)")
    
    same_type_merged = all(
        item["affected_queries"] >= 1 for item in overview
    )
    assert same_type_merged, "同类问题应合并展示"

run_test("批量报告诊断overview", test_report_diagnostic_overview)

# ---- 6. 执行计划JOIN顺序原因 ----

def test_execution_plan_join_order_reason():
    sql = """
    SELECT o.order_id, o.total, c.name
    FROM orders o
    JOIN customers c ON o.customer_id = c.id
    WHERE o.status = 'active'
    ORDER BY o.order_date DESC
    """
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        order_date DATE,
        total DECIMAL(10,2),
        status VARCHAR(20),
        INDEX idx_status (status),
        INDEX idx_customer_id (customer_id),
        INDEX idx_order_date (order_date)
    );
    CREATE TABLE customers (
        id INT PRIMARY KEY,
        name VARCHAR(100)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    plan = plan_gen.generate(sql, schema)
    
    steps = plan["steps"]
    table_steps = [s for s in steps if s.get("node_type") in ("DRIVING_TABLE", "JOINED_TABLE")]
    
    assert len(table_steps) >= 2, f"应有至少2个表步骤: {len(table_steps)}"
    
    for s in table_steps:
        assert "join_order_reason" in s, f"表步骤应有join_order_reason: {s}"
        assert s["join_order_reason"] is not None, f"join_order_reason不应为None"
        print(f"    {s['table']}: {s['join_order_reason']}")
    
    driving = [s for s in table_steps if s["node_type"] == "DRIVING_TABLE"]
    assert len(driving) >= 1, "应有驱动表"
    assert "驱动表" in driving[0]["join_order_reason"], "驱动表应说明原因"
    
    joined = [s for s in table_steps if s["node_type"] == "JOINED_TABLE"]
    if joined:
        assert "被驱动表" in joined[0]["join_order_reason"], "被驱动表应说明原因"
        assert joined[0].get("join_condition") is not None, "被驱动表应有join_condition"

run_test("执行计划JOIN顺序原因", test_execution_plan_join_order_reason)

# ---- 7. 执行计划索引命中原因 ----

def test_execution_plan_index_hit_reason():
    sql = "SELECT * FROM orders WHERE status = 'active'"
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        status VARCHAR(20),
        INDEX idx_status (status)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    plan = plan_gen.generate(sql, schema)
    
    steps = plan["steps"]
    table_steps = [s for s in steps if s.get("node_type") == "DRIVING_TABLE"]
    assert len(table_steps) >= 1
    
    driving = table_steps[0]
    assert driving.get("index_hit_reason") is not None, f"有schema时应有index_hit_reason: {driving}"
    assert "idx_status" in driving["index_hit_reason"], f"应说明命中索引idx_status: {driving['index_hit_reason']}"
    assert "status" in driving["index_hit_reason"], f"应说明匹配列status: {driving['index_hit_reason']}"
    print(f"    索引命中原因: {driving['index_hit_reason']}")
    
    assert driving.get("is_estimated") is False, "有schema时is_estimated应为False"

run_test("执行计划索引命中原因", test_execution_plan_index_hit_reason)

# ---- 8. 无schema时标注估算 ----

def test_execution_plan_estimated_without_schema():
    sql = "SELECT * FROM orders WHERE status = 'active'"
    plan = plan_gen.generate(sql)
    
    steps = plan["steps"]
    table_steps = [s for s in steps if s.get("node_type") == "DRIVING_TABLE"]
    assert len(table_steps) >= 1
    
    driving = table_steps[0]
    assert driving.get("is_estimated") is True, "无schema时is_estimated应为True"
    print(f"    无schema访问类型: {driving['access_type']}, 估算: {driving['is_estimated']}")

run_test("无schema时标注估算", test_execution_plan_estimated_without_schema)

# ---- 9. 执行计划filesort和临时表标记 ----

def test_execution_plan_filesort_temporary():
    sql = """
    SELECT customer_id, COUNT(*) 
    FROM orders 
    WHERE status = 'active' 
    GROUP BY customer_id 
    ORDER BY total DESC
    """
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        total DECIMAL(10,2),
        status VARCHAR(20),
        INDEX idx_status (status)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    plan = plan_gen.generate(sql, schema)
    
    steps = plan["steps"]
    sort_steps = [s for s in steps if s.get("node_type") == "SORT"]
    agg_steps = [s for s in steps if s.get("node_type") == "AGGREGATE"]
    
    if sort_steps:
        sort_step = sort_steps[0]
        assert sort_step.get("join_order_reason") is not None, "排序步骤应有join_order_reason"
        print(f"    排序原因: {sort_step['join_order_reason']}")
    
    if agg_steps:
        agg_step = agg_steps[0]
        print(f"    聚合原因: {agg_step.get('join_order_reason', 'N/A')}")

run_test("执行计划filesort和临时表标记", test_execution_plan_filesort_temporary)

# ---- 10. 回归测试 - 基础功能 ----

def test_regression_basic():
    sql = "SELECT * FROM orders WHERE UPPER(name) = 'TEST'"
    result = engine.analyze(sql)
    
    assert result["statement_type"] == "SELECT"
    assert len(result["suggestions"]) > 0
    assert result["optimized_sql"] is not None
    
    r002 = [s for s in result["suggestions"] if s["rule_id"] == "R002"]
    assert len(r002) >= 1, "UPPER(name) 应触发R002"
    
    r001 = [s for s in result["suggestions"] if s["rule_id"] == "R001"]
    assert len(r001) >= 1, "SELECT * 应触发R001"

run_test("回归测试 - 基础功能", test_regression_basic)

# ---- 11. 多表JOIN + IN子查询改写保留完整JOIN链 ----

def test_multi_join_in_subquery_rewrite():
    sql = """SELECT o.order_id, o.total, c.name
FROM orders o
INNER JOIN customers c ON o.customer_id = c.id
WHERE o.status = 'active' AND o.customer_id IN (
    SELECT cu.customer_id FROM customers cu WHERE cu.country = 'USA'
)
ORDER BY o.order_date DESC, o.total ASC
LIMIT 20 OFFSET 0"""
    result = engine.analyze(sql)
    suggestions = result["suggestions"]
    r003 = [s for s in suggestions if s["rule_id"] == "R003"]
    assert len(r003) >= 1, "多表JOIN+IN子查询应触发R003"

    details = r003[0].get("details", {})
    rewrite_options = details.get("rewrite_options", [])

    join_opt = [o for o in rewrite_options if o["type"] == "JOIN改写"]
    exists_opt = [o for o in rewrite_options if o["type"] == "EXISTS改写"]
    assert len(join_opt) >= 1, "应有JOIN改写方案"
    assert len(exists_opt) >= 1, "应有EXISTS改写方案"

    join_sql = join_opt[0]["sql"]
    assert "INNER JOIN customers c ON" in join_sql, f"JOIN版应保留原INNER JOIN customers c: {join_sql}"
    assert "customers_subq" in join_sql, f"JOIN版应有子查询派生表别名customers_subq: {join_sql}"
    assert "ORDER BY o.order_date DESC" in join_sql, f"JOIN版应保留ORDER BY方向: {join_sql}"
    assert "LIMIT 20 OFFSET 0" in join_sql, f"JOIN版应保留完整LIMIT OFFSET: {join_sql}"
    assert "status" in join_sql, f"JOIN版应保留其他WHERE条件: {join_sql}"

    exists_sql = exists_opt[0]["sql"]
    assert "INNER JOIN customers c ON" in exists_sql, f"EXISTS版应保留原INNER JOIN: {exists_sql}"
    assert "EXISTS" in exists_sql, f"EXISTS版应有EXISTS关键字: {exists_sql}"

    import re
    alias_defs = re.findall(r'(?:JOIN|FROM)\s+(?:\(\s*SELECT[^)]*\)\s+\w+|\w+)\s+(\w+)', exists_sql)
    assert len(alias_defs) == len(set(alias_defs)), f"EXISTS版别名不应重复: {alias_defs}"
    assert "ORDER BY o.order_date DESC" in exists_sql, f"EXISTS版应保留ORDER BY方向: {exists_sql}"

    print(f"    JOIN版: {join_sql[:120]}...")
    print(f"    EXISTS版: {exists_sql[:120]}...")

run_test("多表JOIN+IN子查询改写保留完整JOIN链", test_multi_join_in_subquery_rewrite)

# ---- 12. 同SQL多个WHERE函数 - overview去重但展示问题点数 ----

def test_overview_multiple_same_type_per_sql():
    sql = "SELECT * FROM users WHERE UPPER(name) = 'A' AND LOWER(email) = 'b'"
    result = engine.analyze(sql)

    pr = result["parse_result"]
    where_funcs = pr["where_functions"]
    func_names = [f.get("function", "") for f in where_funcs]
    assert "UPPER" in func_names, f"UPPER应在WHERE函数列表: {func_names}"
    assert "LOWER" in func_names, f"LOWER应在WHERE函数列表: {func_names}"

    report = report_gen.generate([sql])
    overview = report.get("overview", [])
    r002_items = [o for o in overview if o["rule_id"] == "R002"]
    assert len(r002_items) >= 1, "overview应有R002条目"

    r002 = r002_items[0]
    assert r002_items[0]["affected_queries"] == 1, f"同类问题同SQL只应计1条受影响查询: {r002['affected_queries']}"
    assert r002_items[0]["total_occurrences"] >= 2, f"应有至少2个问题点: {r002['total_occurrences']}"

    summaries = r002.get("affected_sql_summaries", [])
    has_multi_marker = any("×2" in s or "×3" in s for s in summaries)
    assert has_multi_marker, f"样例应展示多个同类点标记: {summaries}"

    print(f"    affected_queries: {r002['affected_queries']}, total_occurrences: {r002['total_occurrences']}")
    for s in summaries:
        print(f"    - {s}")

run_test("同SQL多个WHERE函数-overview去重展示", test_overview_multiple_same_type_per_sql)

# ---- 13. GROUP BY有索引时执行计划标注索引消除 ----

def test_group_by_with_index():
    sql = "SELECT status, COUNT(*) FROM orders GROUP BY status"
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        status VARCHAR(20),
        total DECIMAL(10,2),
        INDEX idx_status (status)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    plan = plan_gen.generate(sql, schema)

    steps = plan["steps"]
    agg_steps = [s for s in steps if s.get("node_type") == "AGGREGATE"]
    assert len(agg_steps) >= 1, "应有AGGREGATE步骤"

    agg = agg_steps[0]
    extra = agg.get("extra", "")
    assert "GROUP BY列匹配索引" in extra or "Using index" in extra, \
        f"GROUP BY有索引时应说明匹配索引: {extra}"
    assert "filesort" not in extra.lower() or "无需filesort" in extra, \
        f"GROUP BY有索引时不应有filesort或应标注无需: {extra}"
    print(f"    AGGREGATE extra: {extra}")

run_test("GROUP BY有索引-执行计划标注索引消除", test_group_by_with_index)

# ---- 14. GROUP BY无索引时执行计划明确需临时表和filesort ----

def test_group_by_without_index():
    sql = "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id"
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        status VARCHAR(20),
        total DECIMAL(10,2),
        INDEX idx_status (status)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    plan = plan_gen.generate(sql, schema)

    steps = plan["steps"]
    agg_steps = [s for s in steps if s.get("node_type") == "AGGREGATE"]
    assert len(agg_steps) >= 1, "应有AGGREGATE步骤"

    agg = agg_steps[0]
    extra = agg.get("extra", "")
    assert "Using temporary" in extra, f"GROUP BY无索引时应有Using temporary: {extra}"
    assert "GROUP BY列无可用索引" in extra, f"应说明GROUP BY列无索引: {extra}"
    assert "Using filesort" in extra, f"GROUP BY无索引时应有Using filesort: {extra}"
    print(f"    AGGREGATE extra: {extra}")

run_test("GROUP BY无索引-明确临时表和filesort", test_group_by_without_index)

# 总结
print("\n" + "=" * 80)
print(f"  测试总结: 通过 {passed} / {passed + failed}")
if failed > 0:
    print(f"  ❌ {failed} 个测试失败")
else:
    print(f"  ✅ 所有测试通过!")
print("=" * 80)

sys.exit(0 if failed == 0 else 1)
