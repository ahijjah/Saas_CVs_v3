"""Top-level statement scanning for migration files.

Only top-level statements matter for transaction control. Comments, quoted text and dollar-quoted bodies are skipped, so a
PL/pgSQL BEGIN inside a function body is not mistaken for transaction control. The runner owns the transaction: a migration
may open and close one transaction itself, at its first and last statement, and the runner strips those two statements.
Any other transaction control makes the file unusable and is rejected before anything runs."""
from __future__ import annotations

import re
from dataclasses import dataclass


class SqlTextError(ValueError):
    pass


_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_BEGIN_FORMS = {"BEGIN", "BEGIN WORK", "BEGIN TRANSACTION", "START TRANSACTION"}
_COMMIT_FORMS = {"COMMIT", "COMMIT WORK", "COMMIT TRANSACTION", "END", "END WORK", "END TRANSACTION"}
_OTHER_CONTROL = ("ROLLBACK", "SAVEPOINT", "RELEASE", "PREPARE TRANSACTION", "COMMIT PREPARED",
                  "ROLLBACK PREPARED", "SET TRANSACTION", "SET SESSION CHARACTERISTICS")


@dataclass(frozen=True)
class Statement:
    start: int   # index of the first character after the previous top-level ';'
    end: int     # index of the terminating ';' (or the end of the text)
    text: str    # comment-free, whitespace-normalised, upper-cased statement text


@dataclass(frozen=True)
class MigrationBody:
    sql: str
    began: bool
    committed: bool


def _skip_quoted(sql: str, i: int, quote: str) -> int:
    j = i + 1
    while True:
        k = sql.find(quote, j)
        if k == -1:
            raise SqlTextError("unterminated quoted text")
        if sql.startswith(quote, k + 1):      # doubled quote inside the literal
            j = k + 2
            continue
        return k + 1


def _skip_dollar(sql: str, i: int) -> int | None:
    m = _DOLLAR.match(sql, i)
    if not m:
        return None
    tag = m.group(0)
    j = sql.find(tag, i + len(tag))
    if j == -1:
        raise SqlTextError("unterminated dollar-quoted text")
    return j + len(tag)


def mask_comments(sql: str) -> str:
    """Return sql with comment characters replaced by spaces. Positions are unchanged."""
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "'\"":
            i = _skip_quoted(sql, i, c)
        elif c == "$" and (nxt := _skip_dollar(sql, i)) is not None:
            i = nxt
        else:
            i += 1
    return "".join(out)


def top_level_statements(sql: str) -> list[Statement]:
    """Split on top-level semicolons. The scan runs on the comment-masked text, so an apostrophe inside a comment is never
    read as the start of a quoted literal. Positions refer to the original text, which is the same length."""
    scan = mask_comments(sql)
    statements: list[Statement] = []
    i, n, start = 0, len(scan), 0
    while i < n:
        c = scan[i]
        if c in "'\"":
            i = _skip_quoted(scan, i, c)
            continue
        if c == "$" and (nxt := _skip_dollar(scan, i)) is not None:
            i = nxt
            continue
        if c == ";":
            statements.append(_make(scan, start, i))
            start = i + 1
        i += 1
    statements.append(_make(scan, start, n))
    return [s for s in statements if s.text]


def _make(masked: str, start: int, end: int) -> Statement:
    text = re.sub(r"\s+", " ", masked[start:end]).strip().upper()
    return Statement(start=start, end=end, text=text)


def _is_control(text: str) -> bool:
    return text in _BEGIN_FORMS or text in _COMMIT_FORMS or text.startswith(_OTHER_CONTROL)


def migration_body(sql: str) -> MigrationBody:
    """Return the statements to run inside the runner's own transaction.

    Accepted forms:
      * no transaction control at all;
      * exactly one BEGIN followed by exactly one COMMIT/END, with the COMMIT as the last top-level statement. Both are
        removed; any other statement in the file, before or after the pair's start, runs inside the runner's transaction.
      * exactly one COMMIT/END as the last statement. It is removed.
    Anything else (a second BEGIN, a COMMIT that is not last, a BEGIN without a COMMIT, ROLLBACK, SAVEPOINT, or a
    transaction-mode change) raises SqlTextError, before the file runs."""
    statements = top_level_statements(sql)
    control = [(k, st) for k, st in enumerate(statements) if _is_control(st.text)]
    if not control:
        return MigrationBody(sql=sql, began=False, committed=False)
    last = len(statements) - 1
    forms = [(k, st) for k, st in control]
    for k, st in forms:
        if not (st.text in _BEGIN_FORMS or st.text in _COMMIT_FORMS):
            raise SqlTextError(f"transaction control {st.text[:40]!r} is not allowed in a migration")
    begins = [(k, st) for k, st in forms if st.text in _BEGIN_FORMS]
    commits = [(k, st) for k, st in forms if st.text in _COMMIT_FORMS]
    if len(begins) > 1 or len(commits) > 1:
        raise SqlTextError("a migration may contain at most one BEGIN and one COMMIT")
    if commits and commits[0][0] != last:
        raise SqlTextError("COMMIT must be the last top-level statement of a migration")
    if begins and not commits:
        raise SqlTextError("BEGIN without a matching COMMIT at the end of the file")
    if not begins and commits:
        return MigrationBody(sql=sql[: commits[0][1].start], began=False, committed=True)
    begin_st = begins[0][1]
    commit_st = commits[0][1]
    body = sql[: begin_st.start] + sql[begin_st.end + 1 : commit_st.start]
    return MigrationBody(sql=body, began=True, committed=True)
