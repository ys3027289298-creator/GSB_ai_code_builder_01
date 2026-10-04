from ai_code_builder.versioning import VersionStore


def test_commit_and_history(tmp_path):
    store = VersionStore(tmp_path / "store")
    v1 = store.commit("a.py", "print(1)\n", message="first")
    v2 = store.commit("a.py", "print(2)\n", message="second")
    history = store.history("a.py")
    assert [v.version for v in history] == [1, 2]
    assert v1.sha256 != v2.sha256


def test_idempotent_commit(tmp_path):
    store = VersionStore(tmp_path / "store")
    v1 = store.commit("a.py", "print(1)\n")
    v2 = store.commit("a.py", "print(1)\n")
    assert v1.version == v2.version
    assert len(store.history("a.py")) == 1


def test_diff_between_versions(tmp_path):
    store = VersionStore(tmp_path / "store")
    store.commit("a.py", "print(1)\n")
    store.commit("a.py", "print(2)\n")
    diff = store.diff("a.py", 1, 2)
    assert "-print(1)" in diff
    assert "+print(2)" in diff


def test_restore(tmp_path):
    store = VersionStore(tmp_path / "store")
    store.commit("a.py", "v1\n")
    store.commit("a.py", "v2\n")
    target = tmp_path / "restored.py"
    store.restore("a.py", 1, target)
    assert target.read_text() == "v1\n"


def test_list_files(tmp_path):
    store = VersionStore(tmp_path / "store")
    store.commit("a.py", "1\n")
    store.commit("b.py", "2\n")
    assert store.list_files() == ["a.py", "b.py"]


def test_lookup_missing_version_raises(tmp_path):
    import pytest

    store = VersionStore(tmp_path / "store")
    store.commit("a.py", "1\n")
    with pytest.raises(KeyError):
        store.read("a.py", 99)


def test_duplicate_content_is_idempotent_even_after_newer_version(tmp_path):
    store = VersionStore(tmp_path / "store")
    v1 = store.commit("a.py", "one\n")
    store.commit("a.py", "two\n")
    again = store.commit("a.py", "one\n")
    assert again.version == v1.version
    assert again.sha256 == v1.sha256
    assert [v.version for v in store.history("a.py")] == [1, 2]


def test_rollback_then_commit_stays_consistent(tmp_path):
    store = VersionStore(tmp_path / "store")
    store.commit("a.py", "v1\n")
    store.commit("a.py", "v2\n")
    target = tmp_path / "work" / "a.py"
    store.restore("a.py", 1, target)

    version = store.commit("a.py", target.read_text())

    assert version.version == 1
    assert [v.version for v in store.history("a.py")] == [1, 2]


def test_failed_restore_leaves_workspace_file_untouched(tmp_path):
    import pytest

    store = VersionStore(tmp_path / "store")
    store.commit("a.py", "v1\n")
    target = tmp_path / "a.py"
    target.write_text("keep-me\n")
    with pytest.raises(KeyError):
        store.restore("a.py", 99, target)
    assert target.read_text() == "keep-me\n"
