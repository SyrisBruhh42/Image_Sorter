from __future__ import annotations

import threading
import time

from imagesorter.operation_journal import JournalState, OperationJournal


def test_operation_journal_lifecycle(tmp_path):
    db_file = tmp_path / "journal.db"
    journal = OperationJournal(db_path=str(db_file))

    op_id = "op_test_100"
    src = str(tmp_path / "src.jpg")
    dst = str(tmp_path / "dst.jpg")

    journal.record_intent(op_id, "move", src, dst, task_options={"view_generation": 5})
    entry = journal.get_entry(op_id)
    assert entry is not None
    assert entry["state"] == JournalState.INTENT
    assert entry["action"] == "move"
    assert entry["task_options"]["view_generation"] == 5

    journal.record_staged(op_id, dst, artifacts=[str(tmp_path / ".tmp_staged")])
    entry = journal.get_entry(op_id)
    assert entry["state"] == JournalState.STAGED

    journal.record_committed(op_id, dst, undo_token={"token_id": "t1"})
    entry = journal.get_entry(op_id)
    assert entry["state"] == JournalState.COMMITTED
    assert entry["undo_token"]["token_id"] == "t1"

    journal.record_terminal(op_id, JournalState.COMPLETED)
    entry = journal.get_entry(op_id)
    assert entry["state"] == JournalState.COMPLETED


def test_journal_reconciliation_on_crash_recovery(tmp_path):
    db_file = tmp_path / "journal.db"
    journal = OperationJournal(db_path=str(db_file))

    # Scenario 1: Interrupted at INTENT state (source exists, target not created)
    src1 = tmp_path / "file1.jpg"
    src1.write_text("file 1 content")
    journal.record_intent("op_intent_1", "move", str(src1), str(tmp_path / "dst1.jpg"))

    # Scenario 2: Interrupted at STAGED state with temp artifact
    src2 = tmp_path / "file2.jpg"
    src2.write_text("file 2 content")
    dst2 = tmp_path / "dst2.jpg"
    tmp_art = tmp_path / ".tmp_staged_artifact"
    tmp_art.write_text("temp staged bytes")
    journal.record_intent("op_staged_1", "move", str(src2), str(dst2))
    journal.record_staged("op_staged_1", str(dst2), artifacts=[str(tmp_art)])

    # Scenario 3: Interrupted at COMMITTED state (destination committed, source removed)
    dst3 = tmp_path / "dst3.jpg"
    dst3.write_text("committed content")
    journal.record_intent("op_committed_1", "move", str(tmp_path / "src3.jpg"), str(dst3))
    journal.record_committed("op_committed_1", str(dst3))

    # Reconcile interrupted operations
    reconciled = journal.reconcile_interrupted_operations()
    assert len(reconciled) == 3

    # Verify temp artifact was safely cleaned up
    assert not tmp_art.exists()
    # Verify user's original source files remain intact
    assert src1.exists()
    assert src2.exists()
    assert dst3.exists()

    # Check updated journal states after reconciliation
    e1 = journal.get_entry("op_intent_1")
    assert e1["state"] == JournalState.FAILED

    e2 = journal.get_entry("op_staged_1")
    assert e2["state"] == JournalState.FAILED

    e3 = journal.get_entry("op_committed_1")
    assert e3["state"] == JournalState.COMPLETED


def test_journal_multithreaded_contention(tmp_path):
    db_file = tmp_path / "journal.db"
    journal = OperationJournal(db_path=str(db_file))

    num_threads = 10
    ops_per_thread = 20

    def worker_func(thread_idx: int):
        for i in range(ops_per_thread):
            op_id = f"thread_{thread_idx}_op_{i}"
            journal.record_intent(op_id, "move", f"/src/{op_id}", f"/dst/{op_id}")
            journal.record_committed(op_id, f"/dst/{op_id}")
            journal.record_terminal(op_id, JournalState.COMPLETED)

    threads = [threading.Thread(target=worker_func, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = journal.get_all_entries(limit=1000)
    assert len(entries) == num_threads * ops_per_thread


def test_journal_pruning(tmp_path):
    db_file = tmp_path / "journal.db"
    journal = OperationJournal(db_path=str(db_file))

    # Record completed entry
    journal.record_intent("old_op", "move", "/src/old", "/dst/old")
    journal.record_terminal("old_op", JournalState.COMPLETED)

    # Manually backdate updated_at in database
    with journal._get_connection() as conn:
        conn.execute(
            "UPDATE operations SET updated_at = ? WHERE operation_id = ?;",
            (time.time() - 86400 * 40, "old_op"),
        )
        conn.commit()

    pruned = journal.prune_journal(max_age_seconds=86400 * 30)
    assert pruned == 1
    assert journal.get_entry("old_op") is None
