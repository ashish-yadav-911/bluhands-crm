"""Serializes the heavy part of provisioning across Celery workers.

`bench new-site` builds ~1000 tables and pegs the box for about three
minutes; the local machine has 7.7 GB of RAM. Two of them at once do not
finish in half the time each, they thrash. Running the worker at
`--concurrency=1` prevents that for one worker process, but not for a second
one someone starts later, so the constraint is enforced where it is needed
rather than only in a run command.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import redis

from app.config import settings

SITE_CREATION_LOCK_NAME = "bluhands:lock:site-creation"


class SiteCreationLockTimeout(RuntimeError):
    pass


@contextmanager
def site_creation_lock() -> Iterator[None]:
    """Hold the bench-wide site creation lock.

    `timeout` is the lease, not a wait: if the holder dies without releasing
    (a killed worker), the lock expires on its own instead of wedging every
    later tenant. It is sized to the site-creation budget plus the wizard and
    key minting that run under it. `blocking_timeout` is how long a queued
    tenant waits its turn.
    """
    client = redis.Redis.from_url(settings.redis_url)
    lock = client.lock(
        SITE_CREATION_LOCK_NAME,
        timeout=settings.site_create_timeout_seconds + 900,
        blocking_timeout=settings.site_create_lock_wait_seconds,
    )
    if not lock.acquire():
        raise SiteCreationLockTimeout(
            f"Another site is being created and the lock was not released within "
            f"{settings.site_create_lock_wait_seconds}s"
        )
    try:
        yield
    finally:
        try:
            lock.release()
        except redis.exceptions.LockError:
            # The lease expired while we held it. Nothing to release, and
            # raising here would mask the provisioning outcome.
            pass
