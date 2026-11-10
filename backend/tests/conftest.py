import pytest


@pytest.fixture(autouse=True)
def _no_real_recycle_bin(monkeypatch):
    """Tests must never send files to the user's real Recycle Bin. A test that
    wants to exercise recycling replaces this with a recording fake."""
    from photoexplorer.dedup import actions

    def refuse(path):
        raise RuntimeError(f"test tried to use the real Recycle Bin: {path}")

    monkeypatch.setattr(actions, "_recycle", refuse)
