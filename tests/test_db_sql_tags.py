"""The DB node's raw SQL (query / exec): a wired {tag} is bound as a parameter,
never pasted into the SQL text, so a webhook body or an LLM reply wired into it
is always data. Existing templates keep their meaning: `name = '{name}'` still
matches the same rows, now safely."""
from __future__ import annotations

import asyncio

import pytest

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.nodes.core.builtin import _bind_sql_tags
from boltjar.runtime import Runtime
from boltjar.sqlite_store import SqliteStore

INJECTIONS = [
    "x' OR '1'='1",
    "x'; DROP TABLE people; --",
    "' UNION SELECT name, name, name FROM sqlite_master --",
    'x" OR "1"="1',
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    from boltjar import server
    s = SqliteStore(root=tmp_path)
    monkeypatch.setattr(server, "STORE", s)
    s.execute("mydb", "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
    for name, age in (("Ada", 36), ("Alice", 30), ("Bob", 41)):
        s.execute("mydb", "INSERT INTO people(name, age) VALUES(:n, :a)", {"n": name, "a": age})
    return s


def _run(op: str, sql: str, **tags):
    """Fire one DB node, each tag wired from a Text node (an Integer for an int)."""
    nodes = [{"id": "mydb", "type": "core.store.database", "config": {}},
             {"id": "node", "type": "core.db", "config": {"operation": op, "sql": sql}}]
    edges = [{"src": "mydb", "src_port": "db", "dst": "node", "dst_port": "db"}]
    for name, value in tags.items():
        source = ({"type": "core.value.integer", "config": {"number": value}} if isinstance(value, int)
                  else {"type": "core.value.text", "config": {"text": value}})
        nodes.append({"id": name, **source})
        edges.append({"src": name, "src_port": "out", "dst": "node", "dst_port": name})
    rt = Runtime()
    rt.build({"nodes": nodes, "edges": edges})
    asyncio.run(rt._fire(rt.nodes["node"], "trigger", "go", rt.new_turn()))
    return rt.nodes["node"].out_latch


# ---------------------------------------------------------------- what binds where

def test_bare_tag_binds_the_wired_value():
    assert _bind_sql_tags("SELECT * FROM t WHERE id = {row_id}", {"row_id": 7}) == (
        "SELECT * FROM t WHERE id = :_tag0", {"_tag0": 7})


def test_quoted_tag_binds_text_and_keeps_the_literal_around_it():
    assert _bind_sql_tags("WHERE name = '{name}'", {"name": 5}) == (
        "WHERE name = :_tag0", {"_tag0": "5"})
    assert _bind_sql_tags("WHERE name LIKE '%{q}%'", {"q": "it's"}) == (
        "WHERE name LIKE ('%' || :_tag0 || '%')", {"_tag0": "it's"})


def test_double_quoted_tag_is_a_value_too():
    # never an identifier: a value that names a column must not compare to it.
    assert _bind_sql_tags('WHERE name = "{who}"', {"who": "name"}) == (
        "WHERE name = :_tag0", {"_tag0": "name"})
    assert _bind_sql_tags('WHERE name = "Mr {who}"', {"who": 'x" OR 1=1 --'}) == (
        "WHERE name = ('Mr ' || :_tag0)", {"_tag0": 'x" OR 1=1 --'})


def test_tags_in_comments_brackets_and_backticks_are_left_alone():
    sql = "SELECT [{a}], `{a}` FROM t -- {a}\n/* {a} */ WHERE x = {a}"
    assert _bind_sql_tags(sql, {"a": 1}) == (
        "SELECT [{a}], `{a}` FROM t -- {a}\n/* {a} */ WHERE x = :_tag0", {"_tag0": 1})


def test_unwired_tags_and_escaped_quotes_are_untouched():
    sql = "SELECT '{nope}', 'it''s {a}' FROM t"
    assert _bind_sql_tags(sql, {"a": "x"}) == (
        "SELECT '{nope}', ('it''s ' || :_tag0) FROM t", {"_tag0": "x"})


def test_unterminated_quote_is_never_substituted():
    assert _bind_sql_tags("SELECT 'abc {a}", {"a": "x"}) == ("SELECT 'abc {a}", {})


# ---------------------------------------------------------------- through the node

def test_quoted_template_still_matches(store):
    rows = _run("query", "SELECT name, age FROM people WHERE name = '{who}'", who="Ada")["rows"]
    assert rows == [{"name": "Ada", "age": 36}]


def test_bare_template_matches_numbers_and_numeric_text(store):
    for value in (41, "41"):
        rows = _run("query", "SELECT name FROM people WHERE age = {age}", age=value)["rows"]
        assert rows == [{"name": "Bob"}]
    assert len(_run("query", "SELECT name FROM people LIMIT {n}", n="2")["rows"]) == 2


@pytest.mark.parametrize("text, bound", [
    ("7", 7), ("-12", -12), ("3.5", 3.5), ("0.1", 0.1),
    # anything the number would not spell back exactly stays the text it was.
    ("007", "007"), ("+7", "+7"), (" 7", " 7"), ("1e5", "1e5"), ("1_000", "1_000"),
    ("nan", "nan"), ("inf", "inf"), (str(2**63), str(2**63)), ("seven", "seven"),
])
def test_bare_tag_binds_text_that_spells_a_number_as_the_number(text, bound):
    sql, params = _bind_sql_tags("WHERE id = {id}", {"id": text})
    assert params == {"_tag0": bound}
    assert type(params["_tag0"]) is type(bound)
    # in quotes it is always text.
    assert _bind_sql_tags("WHERE id = '{id}'", {"id": text})[1] == {"_tag0": text}


def test_numeric_text_matches_where_nothing_gives_it_a_type(store):
    # an untyped column and json_extract have no affinity to turn '3' into 3: the
    # pasted template compared numbers there, and the bound tag must too.
    store.execute("mydb", "CREATE TABLE events(id, data)")
    store.execute("mydb", """INSERT INTO events VALUES(3, '{"user": 7}')""")
    assert _run("query", "SELECT id FROM events WHERE id = {id}", id="3")["rows"] == [{"id": 3}]
    rows = _run("query", "SELECT id FROM events WHERE json_extract(data, '$.user') = {user}", user="7")["rows"]
    assert rows == [{"id": 3}]
    # quoted, the tag is text, as the quoted literal always was.
    assert _run("query", "SELECT id FROM events WHERE id = '{id}'", id="3")["rows"] == []


def test_like_pattern_matches_a_substring(store):
    rows = _run("query", "SELECT name FROM people WHERE name LIKE '%{q}%' ORDER BY name", q="li")["rows"]
    assert rows == [{"name": "Alice"}]


def test_a_value_naming_a_column_matches_nothing(store):
    # pasted into "{who}", `name` became the column itself and matched every row.
    rows = _run("query", 'SELECT * FROM people WHERE name = "{who}"', who="name")["rows"]
    assert rows == []


@pytest.mark.parametrize("attack", INJECTIONS)
def test_query_injection_matches_nothing(store, attack):
    for sql in ("SELECT * FROM people WHERE name = '{who}'",
                "SELECT * FROM people WHERE name = {who}",
                'SELECT * FROM people WHERE name = "{who}"'):
        assert _run("query", sql, who=attack)["rows"] == [], sql


@pytest.mark.parametrize("attack", INJECTIONS)
def test_exec_injection_is_stored_verbatim(store, attack):
    _run("exec", "INSERT INTO people(name, age) VALUES('{name}', 1)", name=attack)
    assert store.query("mydb", "SELECT name FROM people WHERE age = 1") == [{"name": attack}]
    # the table (and the three rows before it) survived.
    assert store.query("mydb", "SELECT count(*) AS n FROM people") == [{"n": 4}]


def test_secret_tokens_still_resolve_in_sql(store, monkeypatch):
    from boltjar import secrets
    # a store that counts as loaded, so the real secrets file is never read over it.
    monkeypatch.setattr(secrets, "_store", {"DB_TEST_NAME": "Ada"})
    monkeypatch.setattr(secrets, "_loaded", True)
    rows = _run("query", "SELECT age FROM people WHERE name = '{{secret.DB_TEST_NAME}}'")["rows"]
    assert rows == [{"age": 36}]
