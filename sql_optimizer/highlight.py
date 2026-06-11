import re
import difflib


class SQLHighlighter:
    KEYWORDS = {
        "SELECT", "FROM", "WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "FULL",
        "CROSS", "ON", "AND", "OR", "NOT", "IN", "EXISTS", "BETWEEN", "LIKE",
        "IS", "NULL", "AS", "GROUP", "BY", "ORDER", "ASC", "DESC", "HAVING",
        "LIMIT", "OFFSET", "UNION", "ALL", "DISTINCT", "INSERT", "UPDATE",
        "DELETE", "CREATE", "ALTER", "DROP", "INDEX", "TABLE", "INTO",
        "VALUES", "SET", "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "CONSTRAINT",
        "DEFAULT", "NOT", "NULL", "UNIQUE", "CHECK", "AUTO_INCREMENT",
        "IF", "ELSE", "CASE", "WHEN", "THEN", "END", "BEGIN", "COMMIT",
        "ROLLBACK", "GRANT", "REVOKE", "WITH", "RECURSIVE", "OVER",
        "PARTITION", "ROWS", "RANGE", "UNBOUNDED", "PRECEDING", "FOLLOWING",
        "CURRENT", "ROW", "FETCH", "NEXT", "ONLY", "TOP", "OUTER",
        "NATURAL", "USING", "EXCEPT", "INTERSECT", "ANY", "SOME",
        "TRUE", "FALSE", "UNKNOWN", "CAST", "CONVERT", "COALESCE",
        "NULLIF", "ISNULL", "IFNULL",
    }

    FUNCTIONS = {
        "COUNT", "SUM", "AVG", "MIN", "MAX", "UPPER", "LOWER", "TRIM",
        "SUBSTR", "SUBSTRING", "LENGTH", "CHAR_LENGTH", "CONCAT", "REPLACE",
        "REVERSE", "LPAD", "RPAD", "INSTR", "LOCATE", "LEFT", "RIGHT",
        "ROUND", "FLOOR", "CEIL", "CEILING", "ABS", "MOD", "POWER",
        "SQRT", "SIGN", "EXP", "LOG", "LOG10", "NOW", "CURDATE",
        "CURTIME", "DATE", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE",
        "SECOND", "DATE_FORMAT", "DATE_ADD", "DATE_SUB", "DATEDIFF",
        "TIMESTAMPDIFF", "TIMESTAMPADD", "STR_TO_DATE", "GROUP_CONCAT",
        "FIND_IN_SET", "FIELD", "ELT", "ASCII", "ORD", "BIN", "HEX",
        "OCT", "CONV", "FORMAT", "ROW_NUMBER", "RANK", "DENSE_RANK",
        "LEAD", "LAG", "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE",
    }

    def highlight(self, sql: str):
        tokens = self._tokenize(sql)
        highlighted = []
        for token_type, value in tokens:
            escaped_value = self._html_escape(value)
            if token_type == "keyword":
                highlighted.append(
                    f'<span class="sql-keyword">{escaped_value}</span>'
                )
            elif token_type == "function":
                highlighted.append(
                    f'<span class="sql-function">{escaped_value}</span>'
                )
            elif token_type == "string":
                highlighted.append(
                    f'<span class="sql-string">{escaped_value}</span>'
                )
            elif token_type == "number":
                highlighted.append(
                    f'<span class="sql-number">{escaped_value}</span>'
                )
            elif token_type == "comment":
                highlighted.append(
                    f'<span class="sql-comment">{escaped_value}</span>'
                )
            elif token_type == "operator":
                highlighted.append(
                    f'<span class="sql-operator">{escaped_value}</span>'
                )
            elif token_type == "parenthesis":
                highlighted.append(
                    f'<span class="sql-parenthesis">{escaped_value}</span>'
                )
            elif token_type == "comma":
                highlighted.append(
                    f'<span class="sql-comma">{escaped_value}</span>'
                )
            else:
                highlighted.append(escaped_value)

        return "".join(highlighted)

    def compare(self, original_sql: str, optimized_sql: str):
        original_highlighted = self.highlight(original_sql)
        optimized_highlighted = self.highlight(optimized_sql)

        diff_details = self._compute_diff(original_sql, optimized_sql)

        annotated_original, annotated_optimized = self._annotate_diff(
            original_sql, optimized_sql
        )

        return {
            "original": {
                "sql": original_sql,
                "highlighted": original_highlighted,
                "annotated": annotated_original,
            },
            "optimized": {
                "sql": optimized_sql,
                "highlighted": optimized_highlighted,
                "annotated": annotated_optimized,
            },
            "has_changes": original_sql.strip() != optimized_sql.strip(),
            "diff_details": diff_details,
            "diff_summary": self._build_diff_summary(diff_details),
            "css": self._get_css(),
        }

    def _tokenize(self, sql):
        tokens = []
        i = 0
        sql_len = len(sql)

        while i < sql_len:
            if sql[i].isspace():
                j = i
                while j < sql_len and sql[j].isspace():
                    j += 1
                tokens.append(("whitespace", sql[i:j]))
                i = j
                continue

            if sql[i:i+2] == "--":
                j = i
                while j < sql_len and sql[j] != "\n":
                    j += 1
                tokens.append(("comment", sql[i:j]))
                i = j
                continue

            if sql[i:i+2] == "/*":
                end = sql.find("*/", i + 2)
                if end != -1:
                    tokens.append(("comment", sql[i:end + 2]))
                    i = end + 2
                else:
                    tokens.append(("comment", sql[i:]))
                    i = sql_len
                continue

            if sql[i] in ("'", '"'):
                quote = sql[i]
                j = i + 1
                while j < sql_len and sql[j] != quote:
                    if sql[j] == "\\":
                        j += 1
                    j += 1
                if j < sql_len:
                    j += 1
                tokens.append(("string", sql[i:j]))
                i = j
                continue

            if sql[i].isdigit() or (sql[i] == "." and i + 1 < sql_len and sql[i+1].isdigit()):
                j = i
                while j < sql_len and (sql[j].isdigit() or sql[j] == "."):
                    j += 1
                tokens.append(("number", sql[i:j]))
                i = j
                continue

            if sql[i] == "`":
                j = i + 1
                while j < sql_len and sql[j] != "`":
                    j += 1
                if j < sql_len:
                    j += 1
                tokens.append(("identifier", sql[i:j]))
                i = j
                continue

            if sql[i] in ("(", ")"):
                tokens.append(("parenthesis", sql[i]))
                i += 1
                continue

            if sql[i] == ",":
                tokens.append(("comma", sql[i]))
                i += 1
                continue

            if sql[i] in ("=", "<", ">", "!", "+", "-", "*", "/", "%", "|", "&", "^"):
                if sql[i:i+2] in ("!=", "<>", "<=", ">=", "&&", "||"):
                    tokens.append(("operator", sql[i:i+2]))
                    i += 2
                else:
                    tokens.append(("operator", sql[i]))
                    i += 1
                continue

            if sql[i].isalpha() or sql[i] == "_":
                j = i
                while j < sql_len and (sql[j].isalnum() or sql[j] == "_"):
                    j += 1
                word = sql[i:j]
                upper_word = word.upper()

                if upper_word in self.KEYWORDS:
                    tokens.append(("keyword", word))
                elif upper_word in self.FUNCTIONS:
                    tokens.append(("function", word))
                else:
                    tokens.append(("identifier", word))
                i = j
                continue

            tokens.append(("unknown", sql[i]))
            i += 1

        return tokens

    def _annotate_diff(self, original, optimized):
        orig_words = self._tokenize_for_diff(original)
        opt_words = self._tokenize_for_diff(optimized)

        sm = difflib.SequenceMatcher(None, orig_words, opt_words)

        annotated_original = []
        annotated_optimized = []

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for w in orig_words[i1:i2]:
                    annotated_original.append(self._token_for_display(w))
                for w in opt_words[j1:j2]:
                    annotated_optimized.append(self._token_for_display(w))
            elif tag == "replace":
                for w in orig_words[i1:i2]:
                    annotated_original.append(
                        f'<span class="sql-diff-removed">{self._token_for_display(w)}</span>'
                    )
                for w in opt_words[j1:j2]:
                    annotated_optimized.append(
                        f'<span class="sql-diff-added">{self._token_for_display(w)}</span>'
                    )
            elif tag == "delete":
                for w in orig_words[i1:i2]:
                    annotated_original.append(
                        f'<span class="sql-diff-removed">{self._token_for_display(w)}</span>'
                    )
            elif tag == "insert":
                for w in opt_words[j1:j2]:
                    annotated_optimized.append(
                        f'<span class="sql-diff-added">{self._token_for_display(w)}</span>'
                    )

        return "".join(annotated_original), "".join(annotated_optimized)

    def _tokenize_for_diff(self, sql):
        tokens = []
        for token_type, value in self._tokenize(sql):
            if token_type == "whitespace":
                tokens.append(" ")
            else:
                tokens.append(value)
        return tokens

    def _token_for_display(self, token):
        return self._html_escape(token)

    def _compute_diff(self, original, optimized):
        orig_words = original.split()
        opt_words = optimized.split()

        details = []
        orig_set = set(w.upper() for w in orig_words)
        opt_set = set(w.upper() for w in opt_words)

        added = opt_set - orig_set
        removed = orig_set - opt_set

        for word in added:
            details.append({"type": "added", "word": word})
        for word in removed:
            details.append({"type": "removed", "word": word})

        return details

    def _build_diff_summary(self, diff_details):
        added = [d["word"] for d in diff_details if d["type"] == "added"]
        removed = [d["word"] for d in diff_details if d["type"] == "removed"]

        changes = []
        if added:
            changes.append(f"新增: {', '.join(added)}")
        if removed:
            changes.append(f"删除: {', '.join(removed)}")

        return {
            "added": added,
            "removed": removed,
            "added_count": len(added),
            "removed_count": len(removed),
            "changes": changes,
        }

    def _html_escape(self, text):
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _get_css(self):
        return """
.sql-keyword { color: #0000ff; font-weight: bold; }
.sql-function { color: #880000; }
.sql-string { color: #008800; }
.sql-number { color: #ff00ff; }
.sql-comment { color: #888888; font-style: italic; }
.sql-operator { color: #333333; font-weight: bold; }
.sql-parenthesis { color: #666666; }
.sql-comma { color: #666666; }
.sql-diff-added {
  background-color: #e6ffed;
  border: 1px solid #34d058;
  border-radius: 3px;
  padding: 0 2px;
  margin: 0 1px;
}
.sql-diff-removed {
  background-color: #ffeef0;
  border: 1px solid #d73a49;
  border-radius: 3px;
  padding: 0 2px;
  margin: 0 1px;
  text-decoration: line-through;
  opacity: 0.7;
}
"""
