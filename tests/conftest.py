"""Shared test isolation for repository-local state."""

import pytest

import allin1_sdk.detector as detector


@pytest.fixture(autouse=True)
def isolate_detector_cache(tmp_path, monkeypatch):
    """Never let path detection tests overwrite the real .gta_path marker."""
    monkeypatch.setattr(detector, "_project_root", lambda: tmp_path)
