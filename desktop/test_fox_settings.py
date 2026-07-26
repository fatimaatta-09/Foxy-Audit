"""Secret-storage behaviour of FoxSettings (D0.1).

The hard rule: a secret either lands in the OS keychain, or the caller is told
it did NOT save. There is no plaintext QSettings fallback on any path.

QSettings is redirected to a temp INI file so these tests never touch the real
registry / user config.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings

import fox_settings
from fox_settings import FoxSettings


@pytest.fixture(autouse=True)
def _temp_settings(tmp_path):
    """Point QSettings at a throwaway INI tree for the whole test."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path))
    yield


class _Store:
    """Test double for the keychain."""

    def __init__(self, *, persistent=True, accept=True):
        self.persistent = persistent
        self._accept = accept
        self.data: dict[str, str] = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        if not self._accept:
            return False
        self.data[name] = value
        return True

    def delete(self, name):
        self.data.pop(name, None)


def _settings(store) -> FoxSettings:
    s = FoxSettings()
    s._secrets = store
    return s


def test_successful_keychain_write_reports_true_and_stores_nothing_plaintext():
    store = _Store()
    s = _settings(store)
    assert s.set_org_api_key("foxy_sk_secret") is True
    assert store.data["org_api_key"] == "foxy_sk_secret"
    assert s.org_api_key() == "foxy_sk_secret"
    assert not s._s.contains("foxy/org_key")       # never plaintext


def test_refused_keychain_write_reports_false_and_writes_no_plaintext():
    store = _Store(accept=False)
    s = _settings(store)
    assert s.set_org_api_key("foxy_sk_secret") is False
    assert store.data == {}
    assert not s._s.contains("foxy/org_key")       # NOT persisted anywhere
    assert s.org_api_key() == ""


def test_memory_only_store_reports_false_because_it_will_not_survive():
    """A non-persistent store (no keychain backend) holds the value in process
    memory only — the caller must be told it did not really save."""
    store = _Store(persistent=False)
    s = _settings(store)
    assert s.set_org_api_key("foxy_sk_secret") is False
    assert not s._s.contains("foxy/org_key")


def test_ai_provider_key_follows_the_same_contract():
    ok, bad = _Store(), _Store(accept=False)
    assert _settings(ok).set_api_key("openai", "sk-live") is True
    assert _settings(bad).set_api_key("openai", "sk-live") is False


def test_legacy_plaintext_is_migrated_then_scrubbed_when_durable():
    store = _Store()
    s = _settings(store)
    s._s.setValue("foxy/org_key", "legacy_plain_key")   # written by an old build
    assert s.org_api_key() == "legacy_plain_key"
    assert store.data["org_api_key"] == "legacy_plain_key"
    assert not s._s.contains("foxy/org_key")            # plaintext scrubbed


def test_legacy_plaintext_is_kept_when_the_store_cannot_persist():
    """Migrating into a memory-only store must NOT delete the only durable
    copy — that would lose the user's key on the next launch."""
    store = _Store(persistent=False)
    s = _settings(store)
    s._s.setValue("foxy/org_key", "legacy_plain_key")
    assert s.org_api_key() == "legacy_plain_key"
    assert s._s.value("foxy/org_key", "", type=str) == "legacy_plain_key"


def test_clearing_a_secret_removes_both_copies_and_succeeds():
    store = _Store()
    s = _settings(store)
    s._s.setValue("foxy/org_key", "legacy")
    store.data["org_api_key"] = "current"
    assert s.set_org_api_key("") is True
    assert store.data == {}
    assert not s._s.contains("foxy/org_key")


def test_geometry_keys_are_plain_and_hold_no_secrets():
    from PyQt6.QtCore import QPoint, QSize
    s = _settings(_Store())
    s.set_pet_pos(QPoint(120, 340))
    s.set_chat_size(QSize(400, 600))
    assert s.pet_pos() == QPoint(120, 340)
    assert s.chat_size() == QSize(400, 600)
    s.clear_pet_pos()
    assert s.pet_pos() is None
