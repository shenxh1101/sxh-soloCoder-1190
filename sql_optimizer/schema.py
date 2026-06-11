import re
import json


class TableInfo:
    def __init__(self, name):
        self.name = name
        self.columns = []
        self.indexes = []
        self.primary_key = []
        self.foreign_keys = []
        self.unique_constraints = []

    def to_dict(self):
        return {
            "name": self.name,
            "columns": self.columns,
            "indexes": self.indexes,
            "primary_key": self.primary_key,
            "foreign_keys": self.foreign_keys,
            "unique_constraints": self.unique_constraints,
        }


class ForeignKey:
    def __init__(self, name, columns, ref_table, ref_columns):
        self.name = name
        self.columns = columns
        self.ref_table = ref_table
        self.ref_columns = ref_columns

    def to_dict(self):
        return {
            "name": self.name,
            "columns": self.columns,
            "referenced_table": self.ref_table,
            "referenced_columns": self.ref_columns,
        }


class SchemaParser:
    def __init__(self):
        self.column_type_pattern = re.compile(
            r'(\w+)\s+('
            r'BIGINT|INT|INTEGER|SMALLINT|TINYINT|MEDIUMINT|'
            r'FLOAT|DOUBLE|DECIMAL|NUMERIC|REAL|'
            r'VARCHAR|CHAR|TEXT|TINYTEXT|MEDIUMTEXT|LONGTEXT|NTEXT|NVARCHAR|NCHAR|'
            r'DATE|DATETIME|TIMESTAMP|TIME|YEAR|'
            r'BLOB|TINYBLOB|MEDIUMBLOB|LONGBLOB|'
            r'BOOLEAN|BOOL|BIT|ENUM|SET|JSON|BINARY|VARBINARY|IMAGE|'
            r'UUID|GUID|SERIAL|BIGSERIAL|SMALLSERIAL'
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

        ddl_text = re.sub(r'--.*$', '', ddl_text, flags=re.MULTILINE)
        ddl_text = re.sub(r'/\*[\s\S]*?\*/', '', ddl_text)

        table_blocks = re.findall(
            r'CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?\s*\((.*?)\)\s*(?:ENGINE|DEFAULT|CHARSET|COLLATE|AUTO_INCREMENT|COMMENT|PARTITION|;|$)',
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

                upper_line = line.upper()

                if upper_line.startswith("PRIMARY KEY"):
                    pk_match = re.match(
                        r'PRIMARY\s+KEY\s*(?:\(\s*`?(\w+)`?\s*\))?\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if pk_match:
                        pk_cols = [c.strip().strip('`"[]') for c in pk_match.group(2).split(",")]
                        table.primary_key = pk_cols
                        table.indexes.append({
                            "name": "PRIMARY",
                            "columns": pk_cols,
                            "type": "PRIMARY",
                            "is_composite": len(pk_cols) > 1,
                        })
                    else:
                        pk_match2 = re.match(
                            r'PRIMARY\s+KEY\s*\(([^)]+)\)',
                            line,
                            re.IGNORECASE,
                        )
                        if pk_match2:
                            pk_cols = [c.strip().strip('`"[]') for c in pk_match2.group(1).split(",")]
                            table.primary_key = pk_cols
                            table.indexes.append({
                                "name": "PRIMARY",
                                "columns": pk_cols,
                                "type": "PRIMARY",
                                "is_composite": len(pk_cols) > 1,
                            })
                    continue

                if upper_line.startswith("CONSTRAINT") and "FOREIGN KEY" in upper_line:
                    fk_match = re.match(
                        r'CONSTRAINT\s+[`"\[]?(\w+)[`"\]]?\s+FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if fk_match:
                        fk_name = fk_match.group(1)
                        fk_cols = [c.strip().strip('`"[]') for c in fk_match.group(2).split(",")]
                        ref_table = fk_match.group(3)
                        ref_cols = [c.strip().strip('`"[]') for c in fk_match.group(4).split(",")]
                        table.foreign_keys.append(
                            ForeignKey(fk_name, fk_cols, ref_table, ref_cols).to_dict()
                        )
                    continue

                if upper_line.startswith("FOREIGN KEY"):
                    fk_match = re.match(
                        r'FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if fk_match:
                        fk_cols = [c.strip().strip('`"[]') for c in fk_match.group(1).split(",")]
                        ref_table = fk_match.group(2)
                        ref_cols = [c.strip().strip('`"[]') for c in fk_match.group(3).split(",")]
                        table.foreign_keys.append(
                            ForeignKey(f"fk_{table_name}_{'_'.join(fk_cols)}", fk_cols, ref_table, ref_cols).to_dict()
                        )
                    continue

                if upper_line.startswith("UNIQUE"):
                    if "INDEX" in upper_line or "KEY" in upper_line:
                        unique_idx_match = re.match(
                            r'UNIQUE\s+(?:INDEX|KEY)\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
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
                                "is_composite": len(idx_cols) > 1,
                            })
                            table.unique_constraints.append({
                                "name": idx_name,
                                "columns": idx_cols,
                            })
                            continue
                    else:
                        unique_constraint_match = re.match(
                            r'UNIQUE\s*\(([^)]+)\)',
                            line,
                            re.IGNORECASE,
                        )
                        if unique_constraint_match:
                            cols = [c.strip().strip('`"[]') for c in unique_constraint_match.group(1).split(",")]
                            constraint_name = f"uc_{table_name}_{'_'.join(cols)}"
                            table.unique_constraints.append({
                                "name": constraint_name,
                                "columns": cols,
                            })
                            table.indexes.append({
                                "name": constraint_name,
                                "columns": cols,
                                "type": "UNIQUE",
                                "is_composite": len(cols) > 1,
                            })
                            continue

                if upper_line.startswith("CONSTRAINT") and "UNIQUE" in upper_line:
                    const_unique_match = re.match(
                        r'CONSTRAINT\s+[`"\[]?(\w+)[`"\]]?\s+UNIQUE\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if const_unique_match:
                        const_name = const_unique_match.group(1)
                        cols = [c.strip().strip('`"[]') for c in const_unique_match.group(2).split(",")]
                        table.unique_constraints.append({
                            "name": const_name,
                            "columns": cols,
                        })
                        table.indexes.append({
                            "name": const_name,
                            "columns": cols,
                            "type": "UNIQUE",
                            "is_composite": len(cols) > 1,
                        })
                        continue

                if upper_line.startswith("INDEX") or upper_line.startswith("KEY"):
                    idx_match = re.match(
                        r'(?:INDEX|KEY)\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if idx_match:
                        idx_name = idx_match.group(1) or "unnamed_index"
                        idx_cols = [c.strip().strip('`"[]') for c in idx_match.group(2).split(",")]
                        table.indexes.append({
                            "name": idx_name,
                            "columns": idx_cols,
                            "type": "INDEX",
                            "is_composite": len(idx_cols) > 1,
                        })
                        continue

                if upper_line.startswith("FULLTEXT") or upper_line.startswith("FULL TEXT"):
                    ft_match = re.match(
                        r'FULLTEXT\s*(?:INDEX|KEY)?\s*[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if ft_match:
                        idx_name = ft_match.group(1) or "ft_unnamed"
                        idx_cols = [c.strip().strip('`"[]') for c in ft_match.group(2).split(",")]
                        table.indexes.append({
                            "name": idx_name,
                            "columns": idx_cols,
                            "type": "FULLTEXT",
                            "is_composite": len(idx_cols) > 1,
                        })
                        continue

                if upper_line.startswith("SPATIAL"):
                    sp_match = re.match(
                        r'SPATIAL\s*(?:INDEX|KEY)?\s*[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                        line,
                        re.IGNORECASE,
                    )
                    if sp_match:
                        idx_name = sp_match.group(1) or "sp_unnamed"
                        idx_cols = [c.strip().strip('`"[]') for c in sp_match.group(2).split(",")]
                        table.indexes.append({
                            "name": idx_name,
                            "columns": idx_cols,
                            "type": "SPATIAL",
                            "is_composite": len(idx_cols) > 1,
                        })
                        continue

                col_match = re.match(
                    r'[`"\[]?(\w+)[`"\]]?\s+('
                    r'BIGINT|INT|INTEGER|SMALLINT|TINYINT|MEDIUMINT|'
                    r'FLOAT|DOUBLE|DECIMAL|NUMERIC|REAL|'
                    r'VARCHAR|CHAR|TEXT|TINYTEXT|MEDIUMTEXT|LONGTEXT|NTEXT|NVARCHAR|NCHAR|'
                    r'DATE|DATETIME|TIMESTAMP|TIME|YEAR|'
                    r'BLOB|TINYBLOB|MEDIUMBLOB|LONGBLOB|'
                    r'BOOLEAN|BOOL|BIT|ENUM|SET|JSON|BINARY|VARBINARY|IMAGE|'
                    r'UUID|GUID|SERIAL|BIGSERIAL|SMALLSERIAL'
                    r')(?:\s*\([^)]+\))?',
                    line,
                    re.IGNORECASE,
                )
                if col_match:
                    col_name = col_match.group(1)
                    col_type = col_match.group(2).upper()
                    is_pk = bool(re.search(r'\bPRIMARY\s+KEY\b', line, re.IGNORECASE))
                    is_unique = bool(re.search(r'\bUNIQUE\b', line, re.IGNORECASE))
                    has_auto_increment = bool(re.search(r'AUTO_INCREMENT|IDENTITY|SERIAL', line, re.IGNORECASE))

                    col_info = {"name": col_name, "type": col_type}

                    if re.search(r'\bNOT\s+NULL\b', line, re.IGNORECASE):
                        col_info["nullable"] = False
                    else:
                        col_info["nullable"] = True

                    default_match = re.search(
                        r"DEFAULT\s+('(?:[^']*)'|\S+)",
                        line,
                        re.IGNORECASE,
                    )
                    if default_match:
                        col_info["default"] = default_match.group(1).strip("'\"")

                    comment_match = re.search(
                        r"COMMENT\s+'([^']*)'",
                        line,
                        re.IGNORECASE,
                    )
                    if comment_match:
                        col_info["comment"] = comment_match.group(1)

                    if has_auto_increment:
                        col_info["auto_increment"] = True

                    table.columns.append(col_info)

                    if is_pk:
                        table.primary_key.append(col_name)
                        table.indexes.append({
                            "name": "PRIMARY",
                            "columns": [col_name],
                            "type": "PRIMARY",
                            "is_composite": False,
                        })

                    if is_unique and not is_pk:
                        constraint_name = f"uc_{table_name}_{col_name}"
                        table.unique_constraints.append({
                            "name": constraint_name,
                            "columns": [col_name],
                        })
                        table.indexes.append({
                            "name": constraint_name,
                            "columns": [col_name],
                            "type": "UNIQUE",
                            "is_composite": False,
                        })

                    inline_idx = re.search(r'\bKEY\b|\bINDEX\b', line, re.IGNORECASE)
                    if inline_idx and not is_pk and not is_unique:
                        table.indexes.append({
                            "name": f"idx_{table_name}_{col_name}",
                            "columns": [col_name],
                            "type": "INDEX",
                            "is_composite": False,
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
                if "is_composite" not in col:
                    col = dict(col)
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
                    "is_composite": False,
                })
            elif isinstance(idx, dict):
                idx = dict(idx)
                if "is_composite" not in idx:
                    idx["is_composite"] = len(idx.get("columns", [])) > 1
                normalized_indexes.append(idx)
        result["indexes"] = normalized_indexes

        result["primary_key"] = table.get("primary_key", [])
        result["foreign_keys"] = table.get("foreign_keys", [])
        result["unique_constraints"] = table.get("unique_constraints", [])

        return result
