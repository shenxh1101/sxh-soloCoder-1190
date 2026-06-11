import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, ".")
from sql_optimizer.rules import RuleEngine
from sql_optimizer.schema import SchemaParser

engine = RuleEngine()
schema_parser = SchemaParser()

print("=" * 80)
print("当前 IN 子查询改写输出诊断")
print("=" * 80)

# 复杂场景：子查询带别名，原查询带排序方向和分页
sql = """
SELECT o.order_id, o.total, c.name
FROM orders o
INNER JOIN customers c ON o.customer_id = c.id
WHERE o.status = 'active' AND o.amount > 100 AND o.customer_id IN (
    SELECT c.customer_id 
    FROM customers c 
    WHERE c.country = 'USA' AND c.is_vip = 1
)
ORDER BY o.order_date DESC, o.total ASC
LIMIT 20 OFFSET 0
"""

result = engine.analyze(sql)
r003 = [s for s in result["suggestions"] if s["rule_id"] == "R003"]

if r003:
    details = r003[0].get("details", {})
    opts = details.get("rewrite_options", [])
    for opt in opts:
        print(f"\n--- {opt['type']} ---")
        print(opt["sql"])
        print()
        
        # 检查问题点
        checks = {
            "保留 SELECT 列": "o.order_id" in opt["sql"] and "o.total" in opt["sql"] and "c.name" in opt["sql"],
            "有 FROM 子句": "FROM" in opt["sql"],
            "有 JOIN 子句和 customers 表": "customers" in opt["sql"],
            "保留其他 WHERE 条件(status)": "status" in opt["sql"],
            "保留其他 WHERE 条件(amount)": "amount" in opt["sql"],
            "保留 ORDER BY DESC": "DESC" in opt["sql"],
            "保留 ORDER BY ASC": "ASC" in opt["sql"],
            "保留 LIMIT": "LIMIT 20" in opt["sql"],
            "保留 OFFSET": "OFFSET" in opt["sql"],
        }
        for k, v in checks.items():
            mark = "✅" if v else "❌"
            print(f"  {mark} {k}")
else:
    print("未触发 R003 规则")
    print(f"parse_result.in_subqueries = {result['parse_result']['in_subqueries']}")

print("\n\nparse_result:")
pr = result["parse_result"]
print(f"  where_columns: {pr['where_columns']}")
print(f"  where_clause: {pr['where_clause']}")
print(f"  order_by_columns: {pr['order_by_columns']}")
print(f"  limit_value: {pr['limit_value']}")
print(f"  in_subqueries: {pr['in_subqueries']}")
print(f"  join_conditions: {pr['join_conditions']}")
print(f"  join_types: {pr['join_types']}")
