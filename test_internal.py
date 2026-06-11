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
print("  SQL 优化建议引擎 - 全面增强测试 (内部API)")
print("=" * 80)

# 测试1: 函数名误判字段修复
def test1():
    sql = "SELECT * FROM orders WHERE UPPER(customer_name) = 'JOHN' AND YEAR(order_date) = 2024 AND status = 'active'"
    result = engine.analyze(sql)
    pr = result["parse_result"]
    where_cols = pr["where_columns"]
    where_funcs = pr["where_functions"]
    
    assert "UPPER" not in where_cols, f"UPPER 不应在 WHERE 列中: {where_cols}"
    assert "YEAR" not in where_cols, f"YEAR 不应在 WHERE 列中: {where_cols}"
    assert "status" in where_cols, f"status 应在 WHERE 列中: {where_cols}"
    
    func_names = [f.get("function", "") for f in where_funcs]
    func_args = [f.get("arguments", "") for f in where_funcs]
    assert "UPPER" in func_names, f"UPPER 应被识别为函数: {func_names}"
    assert "YEAR" in func_names, f"YEAR 应被识别为函数: {func_names}"
    assert any("customer_name" in a for a in func_args), f"customer_name 应在函数参数中: {func_args}"
    assert any("order_date" in a for a in func_args), f"order_date 应在函数参数中: {func_args}"

run_test("函数名误判字段修复", test1)

# 测试2: IN子查询识别与改写建议
def test2():
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
    pr = result["parse_result"]
    in_subs = pr["in_subqueries"]
    
    assert len(in_subs) >= 1, f"应识别到 IN 子查询: {in_subs}"
    
    suggestions = result["suggestions"]
    r003 = [s for s in suggestions if s["rule_id"] == "R003"]
    assert len(r003) >= 1, f"应给出 R003 建议: {suggestions}"
    
    details = r003[0].get("details", {})
    rewrite_options = details.get("rewrite_options", [])
    assert len(rewrite_options) >= 2, f"应提供 2 种改写方案: {rewrite_options}"
    
    types = [opt["type"] for opt in rewrite_options]
    assert "JOIN改写" in types, f"应包含 JOIN 改写: {types}"
    assert "EXISTS改写" in types, f"应包含 EXISTS 改写: {types}"

run_test("IN子查询识别与改写建议", test2)

# 测试3: 自动SQL改写草案
def test3():
    sql = "SELECT * FROM orders WHERE status = 'active'"
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        order_date DATE,
        total DECIMAL(10,2),
        status VARCHAR(20),
        INDEX idx_status (status)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    result = engine.analyze(sql, schema)
    
    optimized = result["optimized_sql"]
    summary = result["summary"]
    
    assert "*" not in optimized, f"SELECT * 应被展开: {optimized}"
    assert "LIMIT" in optimized, f"应添加 LIMIT: {optimized}"
    assert summary.get("expanded_star") is True, "应有 expanded_star 标记"
    assert summary.get("added_limit") is True, "应有 added_limit 标记"

run_test("自动SQL改写草案", test3)

# 测试4: 索引建议 - 单列 vs 联合
def test4():
    sql = "SELECT order_id, total FROM orders WHERE customer_id = 123 AND status = 'active' AND order_date > '2024-01-01'"
    schema_ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        order_date DATE,
        total DECIMAL(10,2),
        status VARCHAR(20)
    )
    """
    schema = schema_parser.parse(schema_ddl)
    result = engine.analyze(sql, schema)
    
    suggestions = result["suggestions"]
    r005 = [s for s in suggestions if s["rule_id"] == "R005"]
    
    has_composite = any(s.get("details", {}).get("is_composite") for s in r005)
    assert has_composite, f"多条件查询应建议联合索引: {r005}"

run_test("索引建议 - 单列 vs 联合", test4)

# 测试5: 表结构解析 - 联合索引/唯一索引/主外键
def test5():
    ddl = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY AUTO_INCREMENT,
        customer_id INT NOT NULL,
        product_id INT NOT NULL,
        order_date DATE NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        status VARCHAR(20) NOT NULL,
        UNIQUE KEY uk_order_cust (order_id, customer_id),
        INDEX idx_cust_date (customer_id, order_date),
        CONSTRAINT fk_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
        CONSTRAINT fk_product FOREIGN KEY (product_id) REFERENCES products(id)
    )
    """
    schema = schema_parser.parse(ddl)
    table = schema["tables"][0]
    
    indexes = table["indexes"]
    fks = table["foreign_keys"]
    unique_constraints = table["unique_constraints"]
    
    composite_indexes = [idx for idx in indexes if idx.get("is_composite")]
    assert len(composite_indexes) >= 1, f"应包含联合索引: {indexes}"
    assert len(unique_constraints) >= 1, f"应包含唯一约束: {unique_constraints}"
    assert len(fks) >= 2, f"应包含外键: {fks}"

run_test("表结构解析 - 联合索引/唯一索引/主外键", test5)

# 测试6: 执行计划 - 有/无 schema 差异
def test6():
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
    
    plan_no_schema = plan_gen.generate(sql)
    plan_with_schema = plan_gen.generate(sql, schema)
    
    assert plan_no_schema["has_schema"] is False
    assert plan_with_schema["has_schema"] is True
    
    steps_with = plan_with_schema["steps"]
    types_with = [s["access_type"] for s in steps_with]
    
    steps_no = plan_no_schema["steps"]
    types_no = [s["access_type"] for s in steps_no]
    
    assert any("ref" in t for t in types_with) or any("index" in t for t in types_with), \
        f"有 schema 时应使用索引: {types_with}"

run_test("执行计划 - 有/无 schema 差异", test6)

# 测试7: SQL对比 - diff标注新增/删除
def test7():
    original_sql = "SELECT * FROM orders WHERE status = 'active'"
    optimized_sql = "SELECT order_id, customer_id, total FROM orders WHERE status = 'active' LIMIT 100"
    
    result = highlighter.compare(original_sql, optimized_sql)
    diff_summary = result["diff_summary"]
    
    assert "added" in diff_summary, "应有 added 统计"
    assert "removed" in diff_summary, "应有 removed 统计"
    assert len(diff_summary["added"]) > 0, f"应有新增内容: {diff_summary}"
    assert len(diff_summary["removed"]) > 0, f"应有删除内容: {diff_summary}"

run_test("SQL对比 - diff标注新增/删除", test7)

# 测试8: 批量报告 - 按问题类型汇总
def test8():
    sql_list = [
        "SELECT * FROM orders WHERE UPPER(name) = 'TEST'",
        "SELECT o.id FROM orders o WHERE o.customer_id IN (SELECT id FROM customers WHERE country = 'USA')",
        "SELECT * FROM customers WHERE country = 'USA' ORDER BY signup_date",
        "SELECT COUNT(*) FROM orders GROUP BY status HAVING COUNT(*) > 100"
    ]
    
    report = report_gen.generate(sql_list)
    
    assert "global_issue_types" in report, "应有 global_issue_types"
    assert "global_issue_categories" in report, "应有 global_issue_categories"
    assert "global_antipatterns" in report, "应有 global_antipatterns"
    assert "prioritized_actions" in report, "应有 prioritized_actions"
    assert "score_grade" in report, "应有 score_grade"

run_test("批量报告 - 按问题类型汇总", test8)

# 测试9: HAVING 检测
def test9():
    sql1 = "SELECT status, COUNT(*) FROM orders GROUP BY status HAVING COUNT(*) > 100"
    sql2 = "SELECT * FROM orders HAVING total > 1000"
    
    result1 = engine.analyze(sql1)
    result2 = engine.analyze(sql2)
    
    assert result1["parse_result"]["has_having"] is True
    assert result2["parse_result"]["has_having"] is True
    
    r013_1 = [s for s in result1["suggestions"] if s["rule_id"] == "R013"]
    r013_2 = [s for s in result2["suggestions"] if s["rule_id"] == "R013"]
    
    assert len(r013_2) >= 1, f"无 GROUP BY 的 HAVING 应触发 R013: {result2['suggestions']}"

run_test("HAVING 检测", test9)

# 测试10: ORDER BY / GROUP BY 识别
def test10():
    sql = """
    SELECT customer_id, COUNT(*) as cnt, SUM(total) as total
    FROM orders
    WHERE status = 'active'
    GROUP BY customer_id
    HAVING COUNT(*) > 10
    ORDER BY total DESC
    LIMIT 10
    """
    result = engine.analyze(sql)
    pr = result["parse_result"]
    
    assert "customer_id" in pr["group_by_columns"], f"GROUP BY 列应包含 customer_id: {pr['group_by_columns']}"
    assert "total" in pr["order_by_columns"], f"ORDER BY 列应包含 total: {pr['order_by_columns']}"
    assert pr["has_having"] is True
    assert "COUNT" in pr["aggregate_functions"], f"聚合函数应包含 COUNT: {pr['aggregate_functions']}"
    assert "SUM" in pr["aggregate_functions"], f"聚合函数应包含 SUM: {pr['aggregate_functions']}"
    assert pr["limit_value"] == 10, f"LIMIT 值应为 10: {pr['limit_value']}"

run_test("ORDER BY / GROUP BY 识别", test10)

# 总结
print("\n" + "=" * 80)
print(f"  测试总结: 通过 {passed} / {passed + failed}")
if failed > 0:
    print(f"  ❌ {failed} 个测试失败")
else:
    print(f"  ✅ 所有测试通过!")
print("=" * 80)

sys.exit(0 if failed == 0 else 1)
