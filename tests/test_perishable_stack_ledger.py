import threading

import pytest

import perishable_stack_ledger as pl

EMPTY_STATS = {"added": 0, "consumed": 0, "expired": 0, "splits": 0, "merges": 0}


def ledger(**kwargs):
    return pl.PerishableStackLedger(**kwargs)


def test_add_creates_batch_with_fields():
    book = ledger()
    batch = book.add_batch("bag", "apple", 5, 100)
    assert (batch.cell, batch.item_id, batch.expires_at, batch.quantity) == (
        "bag", "apple", 100, 5)
    assert isinstance(batch.batch_id, str) and batch.batch_id
    assert book.inspect("bag") == [batch]
    assert book.total_quantity("bag", "apple") == 5
    assert book.cell_usage() == 1
    assert book.stats() == {"added": 5, "consumed": 0, "expired": 0,
                            "splits": 0, "merges": 0}


def test_add_merges_within_tolerance():
    book = ledger(tolerance=2)
    book.add_batch("bag", "apple", 2, 100)
    merged = book.add_batch("bag", "apple", 3, 101)
    assert (merged.expires_at, merged.quantity) == (100, 5)
    assert len(book.inspect("bag")) == 1
    assert book.total_quantity("bag", "apple") == 5
    assert book.stats()["merges"] == 1
    assert book.stats()["added"] == 5


def test_add_rejects_nonpositive_quantity():
    book = ledger()
    for bad in (0, -3):
        with pytest.raises(pl.LedgerError) as info:
            book.add_batch("bag", "apple", bad, 100)
        assert info.value.code == "LEDGER_INVALID_QUANTITY"
    assert book.total_quantity("bag", "apple") == 0
    assert book.cell_usage() == 0


def test_stack_limit_is_hard_cap():
    book = ledger(stack_limit=5)
    book.add_batch("bag", "apple", 4, 100)
    with pytest.raises(pl.LedgerError) as info:
        book.add_batch("bag", "apple", 2, 100)
    assert info.value.code == "LEDGER_STACK_LIMIT"
    assert book.total_quantity("bag", "apple") == 4
    assert book.inspect("bag")[0].quantity == 4


def test_cell_capacity_is_hard_cap():
    book = ledger(cell_capacity=2)
    book.add_batch("c1", "apple", 1, 100)
    book.add_batch("c2", "apple", 1, 100)
    with pytest.raises(pl.LedgerError) as info:
        book.add_batch("c3", "apple", 1, 100)
    assert info.value.code == "LEDGER_CAPACITY"
    assert book.cell_usage() == 2
    assert book.inspect("c3") == []


def test_consume_takes_earliest_expiry_first():
    book = ledger()
    book.add_batch("bag", "apple", 3, 100)
    book.add_batch("bag", "apple", 5, 200)
    assert book.consume("bag", "apple", 2, now=0) == [(100, 2)]
    assert book.total_quantity("bag", "apple") == 6
    assert book.consume("bag", "apple", 2, now=0) == [(100, 1), (200, 1)]
    assert book.total_quantity("bag", "apple") == 4
    assert book.stats()["consumed"] == 4


def test_consume_ignores_expired_batches():
    book = ledger()
    book.add_batch("bag", "apple", 5, 100)
    with pytest.raises(pl.LedgerError) as info:
        book.consume("bag", "apple", 1, now=100)
    assert info.value.code == "LEDGER_SHORTAGE"
    assert book.total_quantity("bag", "apple") == 5
    assert len(book.inspect("bag")) == 1


def test_consume_shortage_is_atomic():
    book = ledger()
    book.add_batch("bag", "apple", 3, 100)
    book.add_batch("bag", "apple", 2, 200)
    with pytest.raises(pl.LedgerError) as info:
        book.consume("bag", "apple", 6, now=0)
    assert info.value.code == "LEDGER_SHORTAGE"
    assert book.total_quantity("bag", "apple") == 5
    assert [batch.quantity for batch in book.inspect("bag")] == [3, 2]
    assert book.stats()["consumed"] == 0


def test_split_keeps_expiry_and_splits_quantity():
    book = ledger()
    original = book.add_batch("bag", "apple", 10, 100)
    piece = book.split_batch("bag", "apple", 100, 4)
    assert piece.expires_at == 100
    assert piece.quantity == 4
    assert piece.batch_id != original.batch_id
    assert sorted(batch.quantity for batch in book.inspect("bag")) == [4, 6]
    assert book.total_quantity("bag", "apple") == 10
    assert book.stats()["splits"] == 1


def test_merge_batches_sums_quantities():
    book = ledger()
    book.add_batch("bag", "apple", 10, 100)
    book.split_batch("bag", "apple", 100, 4)
    assert len(book.inspect("bag")) == 2
    merged = book.merge_batches("bag", "apple", 100, 100)
    assert (merged.expires_at, merged.quantity) == (100, 10)
    assert len(book.inspect("bag")) == 1
    assert book.stats()["merges"] == 1


def test_expire_due_removes_due_batches_and_counts():
    book = ledger()
    book.add_batch("c1", "apple", 3, 100)
    book.add_batch("c2", "apple", 2, 50)
    book.add_batch("c2", "pear", 4, 300)
    due = book.expire_due(100)
    assert due == [("c1", "apple", 3), ("c2", "apple", 2)]
    assert book.inspect("c1") == []
    assert [batch.item_id for batch in book.inspect("c2")] == ["pear"]
    assert book.stats()["expired"] == 5


def test_inspect_keeps_expired_until_cleanup():
    book = ledger()
    book.add_batch("bag", "apple", 5, 100)
    assert len(book.inspect("bag")) == 1
    assert book.total_quantity("bag", "apple") == 5
    assert book.cell_usage() == 1
    assert book.expire_due(500) == [("bag", "apple", 5)]
    assert book.inspect("bag") == []
    assert book.stats()["expired"] == 5


def test_concurrent_consume_never_goes_negative():
    book = ledger()
    book.add_batch("bag", "apple", 10, 500)
    successes = []
    failures = []
    result_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            taken = book.consume("bag", "apple", 2, now=0)
        except pl.LedgerError as exc:
            with result_lock:
                failures.append(exc.code)
            return
        with result_lock:
            successes.append(taken)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 5
    assert failures == ["LEDGER_SHORTAGE"] * 3
    assert successes[0] == [(500, 2)]
    assert book.total_quantity("bag", "apple") == 0
    assert book.stats()["consumed"] == 10


def test_snapshot_restore_roundtrip():
    book = ledger()
    book.add_batch("bag", "apple", 3, 100)
    book.add_batch("bag", "apple", 5, 200)
    book.consume("bag", "apple", 2, now=0)
    book.split_batch("bag", "apple", 200, 2)
    state = book.snapshot()
    clone = ledger()
    clone.restore(state)
    assert clone.inspect("bag") == book.inspect("bag")
    assert clone.stats() == book.stats()
    assert clone.total_quantity("bag", "apple") == 6
    book.add_batch("bag", "pear", 1, 300)
    assert len(clone.inspect("bag")) == 3
    assert clone.total_quantity("bag", "pear") == 0


def test_restore_rejects_bad_state():
    book = ledger()
    book.add_batch("bag", "apple", 5, 100)
    with pytest.raises(pl.LedgerError) as info:
        book.restore({"batches": "nope"})
    assert info.value.code == "LEDGER_SNAPSHOT_INVALID"
    assert book.total_quantity("bag", "apple") == 5
    assert book.stats()["added"] == 5
