from pathlib import Path

import database as db


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test.db"
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def test_kb_entry_crud_and_counts(tmp_path: Path):
    _use_temp_db(tmp_path)
    db.init_db()

    assert db.count_kb_entries(active_only=False) == 0

    entry_id = db.add_kb_entry(category="Services", title="Haircut", content="Basic haircut")
    assert isinstance(entry_id, int)
    assert db.count_kb_entries(active_only=False) == 1
    assert db.count_kb_entries(active_only=True) == 1

    entry = db.get_kb_entry(entry_id)
    assert entry is not None
    assert entry["title"] == "Haircut"

    db.update_kb_entry(entry_id, category="Services", title="Haircut+", content="Updated")
    entry2 = db.get_kb_entry(entry_id)
    assert entry2["content"] == "Updated"

    db.delete_kb_entry(entry_id)
    assert db.get_kb_entry(entry_id) is None
    assert db.count_kb_entries(active_only=False) == 0


def test_conversation_history_is_chronological_even_with_same_timestamp(tmp_path: Path):
    _use_temp_db(tmp_path)
    db.init_db()

    user_id = "u1"
    db.save_message(user_id, "User", "user", "m1")
    db.save_message(user_id, "User", "assistant", "m2")
    history = db.get_conversation_history(user_id, limit=10)

    assert [h["message"] for h in history] == ["m1", "m2"]
