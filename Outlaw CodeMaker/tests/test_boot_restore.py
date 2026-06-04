"""boot_restore failure counter logic (the anti-bricking failsafe)."""
import boot_restore


def test_counter_increment_and_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(boot_restore, "STATE", tmp_path / ".launch_state")
    boot_restore.write_fails(0)
    assert boot_restore.read_fails() == 0
    assert boot_restore.record_attempt() == 1
    assert boot_restore.record_attempt() == 2
    boot_restore.record_success()
    assert boot_restore.read_fails() == 0


def test_missing_state_reads_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(boot_restore, "STATE", tmp_path / "does_not_exist")
    assert boot_restore.read_fails() == 0


def test_max_fails_threshold():
    # The recovery trigger is 3 consecutive failures.
    assert boot_restore.MAX_FAILS == 3
