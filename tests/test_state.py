import json

from modal_orchestrator.state import (
    Status,
    StateStore,
)


def test_creates_empty_store_when_file_missing(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.records() == {}


def test_round_trip_persists_to_disk(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1", "ak-2"])

    reloaded = StateStore(path)
    assert set(reloaded.records().keys()) == {"ak-1", "ak-2"}
    assert reloaded.records()["ak-1"].status == Status.AVAILABLE


def test_ensure_available_is_idempotent_and_does_not_clobber(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    # Re-running ensure_available must not overwrite the in_flight status.
    store.ensure_available(["ak-1", "ak-2"])
    recs = store.records()
    assert recs["ak-1"].status == Status.IN_FLIGHT
    assert recs["ak-2"].status == Status.AVAILABLE


def test_claim_and_mark_ok(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    assert store.records()["ak-1"].status == Status.IN_FLIGHT
    store.mark_ok("ak-1", exit_code=0)
    rec = store.records()["ak-1"]
    assert rec.status == Status.USED_OK
    assert rec.exit_code == 0
    assert rec.finished_at is not None


def test_mark_failed(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    store.mark_failed("ak-1", exit_code=2)
    rec = store.records()["ak-1"]
    assert rec.status == Status.USED_FAILED
    assert rec.exit_code == 2


def test_mark_aborted(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    store.mark_aborted("ak-1")
    assert store.records()["ak-1"].status == Status.USED_ABORTED


def test_remaining_returns_only_claimable(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1", "ak-2", "ak-3", "ak-4"])
    store.claim("ak-2", log_path="logs/ak-2.log")
    store.mark_ok("ak-2", exit_code=0)
    store.claim("ak-3", log_path="logs/ak-3.log")
    store.mark_failed("ak-3", exit_code=1)
    # ak-1 still available, ak-4 still available, ak-2 done, ak-3 done.
    assert set(store.claimable_ids()) == {"ak-1", "ak-4"}


def test_writes_valid_json_with_version_field(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "ak-1" in data["tokens"]


def test_resume_resets_in_flight_to_aborted_by_default(tmp_path):
    """On reload, in_flight tokens are assumed to have been interrupted by a
    crash and are marked used_aborted (not re-claimable). This protects us
    from double-spending credit on Modal-side containers that may still be
    running."""
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    # Simulate crash: don't call mark_ok / mark_failed.
    reloaded = StateStore(path)
    assert reloaded.records()["ak-1"].status == Status.USED_ABORTED


def test_resume_with_retry_aborted_resets_in_flight_to_available(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    reloaded = StateStore(path, retry_aborted=True)
    assert reloaded.records()["ak-1"].status == Status.AVAILABLE


def test_records_include_timestamps(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    rec = store.records()["ak-1"]
    assert rec.claimed_at is not None
    assert rec.log_path == "logs/ak-1.log"
