import pytest
import redis

from app.services.provisioning import locks


class _FakeLock:
    def __init__(self, acquired=True, release_error=None):
        self._acquired = acquired
        self._release_error = release_error
        self.released = False

    def acquire(self):
        return self._acquired

    def release(self):
        self.released = True
        if self._release_error:
            raise self._release_error


class _FakeRedis:
    def __init__(self, lock):
        self._lock = lock
        self.lock_name = None
        self.lock_kwargs = None

    def lock(self, name, **kwargs):
        self.lock_name = name
        self.lock_kwargs = kwargs
        return self._lock


def _patch(monkeypatch, lock):
    fake = _FakeRedis(lock)
    monkeypatch.setattr(locks.redis.Redis, "from_url", staticmethod(lambda url: fake))
    return fake


def test_lock_is_acquired_and_released(monkeypatch):
    lock = _FakeLock()
    fake = _patch(monkeypatch, lock)

    with locks.site_creation_lock():
        assert not lock.released

    assert lock.released
    assert fake.lock_name == locks.SITE_CREATION_LOCK_NAME
    # The lease must outlive the build it protects, or a second tenant
    # starts building while the first is still running.
    assert fake.lock_kwargs["timeout"] > locks.settings.site_create_timeout_seconds


def test_lock_is_released_when_the_body_raises(monkeypatch):
    lock = _FakeLock()
    _patch(monkeypatch, lock)

    with pytest.raises(ValueError):
        with locks.site_creation_lock():
            raise ValueError("build failed")

    assert lock.released


def test_timeout_waiting_for_the_lock_is_an_error(monkeypatch):
    _patch(monkeypatch, _FakeLock(acquired=False))

    with pytest.raises(locks.SiteCreationLockTimeout):
        with locks.site_creation_lock():
            pytest.fail("body must not run without the lock")


def test_expired_lease_does_not_mask_the_outcome(monkeypatch):
    _patch(monkeypatch, _FakeLock(release_error=redis.exceptions.LockError("expired")))

    with locks.site_creation_lock():
        pass
