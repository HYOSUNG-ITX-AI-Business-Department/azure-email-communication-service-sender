import pytest

from app.services.queue import RedisLock


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool):  # type: ignore[override]
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def eval(self, script: str, *, numkeys: int, keys: list[str], args: list[str]):  # type: ignore[override]
        assert numkeys == 1
        key = keys[0]
        token = args[0]
        if self._store.get(key) == token:
            del self._store[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_redis_lock_token_safe_release() -> None:
    r = _FakeRedis()
    lock = RedisLock(r, key_prefix="email:lock:")

    acquired = await lock.acquire("1", token="t1", ttl_seconds=10)
    assert acquired is True

    # Wrong token should not release
    released = await lock.release("1", token="t2")
    assert released is False

    # Correct token releases
    released = await lock.release("1", token="t1")
    assert released is True


@pytest.mark.asyncio
async def test_redis_lock_acquire_nx() -> None:
    r = _FakeRedis()
    lock = RedisLock(r)

    assert await lock.acquire("1", token="t1", ttl_seconds=10) is True
    assert await lock.acquire("1", token="t2", ttl_seconds=10) is False