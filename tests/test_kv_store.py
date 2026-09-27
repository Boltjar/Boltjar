from boltjar.kv_store import KvStore


def test_set_then_get_roundtrip(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("k1", "name", "Ada")
    assert store.get("k1", "name") == "Ada"


def test_get_missing_returns_default(tmp_path):
    store = KvStore(root=tmp_path)
    assert store.get("k1", "absent") is None
    assert store.get("k1", "absent", "fallback") == "fallback"


def test_has_and_delete(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("k1", "a", 1)
    assert store.has("k1", "a") is True
    store.delete("k1", "a")
    assert store.has("k1", "a") is False


def test_delete_is_idempotent(tmp_path):
    store = KvStore(root=tmp_path)
    # deleting an absent key must not raise
    store.delete("k1", "nope")
    assert store.has("k1", "nope") is False


def test_store_keys_that_clean_alike_do_not_collide(tmp_path):
    # `user.name` and `username` both strip to `username`; they MUST stay distinct
    # stores (a hash of the raw key is appended once any char is normalized).
    store = KvStore(root=tmp_path)
    store.set("user.name", "x", 1)
    store.set("username", "x", 2)
    assert store.get("user.name", "x") == 1
    assert store.get("username", "x") == 2


def test_keys_lists_all(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("k1", "a", 1)
    store.set("k1", "b", 2)
    assert sorted(store.keys("k1")) == ["a", "b"]


def test_values_may_be_any_json(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("k1", "obj", {"nested": [1, 2, 3]})
    assert store.get("k1", "obj") == {"nested": [1, 2, 3]}


def test_persists_to_disk(tmp_path):
    KvStore(root=tmp_path).set("k2", "x", 42)
    assert (tmp_path / "k2.json").exists()


def test_persistence_across_fresh_instances(tmp_path):
    KvStore(root=tmp_path).set("shared", "count", 7)
    # a brand-new instance (empty cache) must read the value back off disk
    assert KvStore(root=tmp_path).get("shared", "count") == 7


def test_atomic_write_leaves_no_temp(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("k3", "a", 1)
    # the .tmp sibling must have been replaced away after the write
    assert not (tmp_path / ".k3.json.tmp").exists()
    assert (tmp_path / "k3.json").exists()


def test_stores_are_isolated_by_key(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("one", "x", "a")
    store.set("two", "x", "b")
    assert store.get("one", "x") == "a"
    assert store.get("two", "x") == "b"


def test_symbol_keys_do_not_collide(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("!!!", "x", "a")
    store.set("###", "x", "b")
    assert store.get("!!!", "x") == "a"
    assert store.get("###", "x") == "b"


def test_non_string_member_key_is_coerced(tmp_path):
    store = KvStore(root=tmp_path)
    store.set("k1", 5, "five")
    # set coerces the member key to str; get with either form resolves it
    assert store.get("k1", "5") == "five"
    assert store.has("k1", 5) is True
