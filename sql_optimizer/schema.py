import re
import json


class TableInfo:
    def __init__(self, name):
        self.name = name
        self.columns = []
        self.indexes = []
        self.primary_key = []

    def to_dict(self):
        return {
            "name": self.name,
            "columns": self.columns,
            "indexes": self.indexes,
            "primary_key": self.primary_key,
        }


class SchemaParser:
    def __init__(self):
        self.column_type_pattern = re.compile(
            r'(\w+)\s+('
            r'BIGINT|INT|INTEGER|SMALLINT|TINYINT|MEDIUMINT|'
            r'FLOAT|DOUBLE|DECIMAL|NUMERIC|'
            r'VARCHAR|CHAR|TEXT|TINYTEXT|MEDIUMTEXT|LONGTEXT|'
            r'DATE|DATETIME|TIMESTAMP|TIME|YEAR|'
            r'BLOB|TINYBLOB|MEDIUMBLOB|LONGBLOB|'
            r'BOOLEAN|BOOL|BIT|ENUM|SET|JSON|BINARY|VARBINARY'
            r')\s*(\([^)]+\))?',
            re.IGNORECASE,
        )

    def parse(self, schema_input):
        if isinstance(schema_input, dict):
            return self._normalize_json_schema(schema_input)
        if isinstance(schema_input, str):
            stripped = schema_input.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    return self._normalize_json_schema(parsed)
                except json.JSONDecodeError:
                    pass
            return self._parse_ddl(stripped)
        return {"tables": []}

    def _parse_ddl(self, ddl_text):
        tables = []
        table_blocks = re.findall(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?\s*\((.*?)\)\s*(?:ENGINE|DEFAULT|CHARSET|COLLATE|AUTO_INCREMENT|COMMENT|;|$)',
            ddl_text,
            re.IGNORECASE | re.DOTALL,
        )

        for table_name, body in table_blocks:
            table = TableInfo(table_name)
            lines = self._split_column_defs(body)

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                pk_match = re.match(
                    r'PRIMARY\s+KEY\s*\(([^)]+)\)', line, re.IGNORECASE
                )
                if pk_match:
                    pk_cols = [c.strip().strip('`"[]') for c in pk_match.group(1).split(",")]
                    table.primary_key = pk_cols
                    table.indexes.append({
                        "name": "PRIMARY",
                        "columns": pk_cols,
                        "type": "PRIMARY",
                    })
                    continue

                idx_match = re.match(
                    r'(?:UNIQUE\s+)?(?:KEY|INDEX)\s+(?:[`"\[]?(\w+)[`"\]]?)\s*\(([^)]+)\)',
                    line,
                    re.IGNORECASE,
                )
                if idx_match:
                    is_unique = bool(re.match(r'UNIQUE', line, re.IGNORECASE))
                    idx_name = idx_match.group(1) or "unnamed_index"
                    idx_cols = [c.strip().strip('`"[]') for c in idx_match.group(2).split(",")]
                    table.indexes.append({
                        "name": idx_name,
                        "columns": idx_cols,
                        "type": "UNIQUE" if is_unique else "INDEX",
                    })
                    continue

                unique_idx_match = re.match(
                    r'UNIQUE\s+(?:KEY|INDEX)\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                    line,
                    re.IGNORECASE,
                )
                if unique_idx_match:
                    idx_name = unique_idx_match.group(1) or "unnamed_unique"
                    idx_cols = [c.strip().strip('`"[]') for c in unique_idx_match.group(2).split(",")]
                    table.indexes.append({
                        "name": idx_name,
                        "columns": idx_cols,
                        "type": "UNIQUE",
                    })
                    continue

                col_match = re.match(
                    r'[`"\[]?(\w+)[`"\]]?\s+('
                    r'BIGINT|INT|INTEGER|SMALLINT|TINYINT|MEDIUMINT|'
                    r'FLOAT|DOUBLE|DECIMAL|NUMERIC|'
                    r'VARCHAR|CHAR|TEXT|TINYTEXT|MEDIUMTEXT|LONGTEXT|'
                    r'DATE|DATETIME|TIMESTAMP|TIME|YEAR|'
                    r'BLOB|TINYBLOB|MEDIUMBLOB|LONGBLOB|'
                    r'BOOLEAN|BOOL|BIT|ENUM|SET|JSON|BINARY|VARBINARY'
                    r')(?:\s*\([^)]+\))?',
                    line,
                    re.IGNORECASE,
                )
                if col_match:
                    col_name = col_match.group(1)
                    col_type = col_match.group(2).upper()
                    is_pk = bool(re.search(r'\bPRIMARY\s+KEY\b', line, re.IGNORECASE))

                    col_info = {"name": col_name, "type": col_type}

                    if re.search(r'\bNOT\s+NULL\b', line, re.IGNORECASE):
                        col_info["nullable"] = False
                    else:
                        col_info["nullable"] = True

                    default_match = re.search(r"DEFAULT\s+('(?:[^']*)'|\S+)", line, re.IGNORECASE)
                    if default_match:
                        col_info["default"] = default_match.group(1)

                    table.columns.append(col_info)

                    if is_pk:
                        table.primary_key.append(col_name)
                        table.indexes.append({
                            "name": "PRIMARY",
                            "columns": [col_name],
                            "type": "PRIMARY",
                        })

                    inline_idx = re.search(r'\bKEY\b|\bINDEX\b', line, re.IGNORECASE)
                    if inline_idx and not is_pk:
                        table.indexes.append({
                            "name": f"idx_{col_name}",
                            "columns": [col_name],
                            "type": "INDEX",
                        })

            tables.append(table.to_dict())

        return {"tables": tables}

    def _split_column_defs(self, body):
        result = []
        depth = 0
        current = []
        for ch in body:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                result.append(''.join(current))
                current = []
            else:
                current.append(ch)
        remaining = ''.join(current).strip()
        if remaining:
            result.append(remaining)
        return result

    def _normalize_json_schema(self, schema):
        if isinstance(schema, list):
            return {"tables": [self._normalize_table(t) for t in schema]}

        if isinstance(schema, dict):
            if "tables" in schema:
                schema["tables"] = [self._normalize_table(t) for t in schema["tables"]]
                return schema
            if "name" in schema:
                return {"tables": [self._normalize_table(schema)]}

        return {"tables": []}

    def _normalize_table(self, table):
        if not isinstance(table, dict):
            return table

        result = {"name": table.get("name", "unknown")}

        columns = table.get("columns", [])
        normalized_columns = []
        for col in columns:
            if isinstance(col, str):
                normalized_columns.append({"name": col, "type": "UNKNOWN"})
            elif isinstance(col, dict):
                normalized_columns.append(col)
        result["columns"] = normalized_columns

        indexes = table.get("indexes", [])
        normalized_indexes = []
        for idx in indexes:
            if isinstance(idx, str):
                normalized_indexes.append({
                    "name": idx,
                    "columns": [idx],
                    "type": "INDEX",
                })
            elif isinstance(idx, dict):
                normalized_indexes.append(idx)
        result["indexes"] = normalized_indexes

        result["primary_key"] = table.get("primary_key", [])

        return result
