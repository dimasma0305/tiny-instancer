from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from instancer.core.cache import redis
from instancer.routes.frontend import router as frontend_router
from instancer.routes.v1.instances import router as instances_router


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, Any]:
    await redis.ping()

    try:
        yield
    finally:
        await redis.close()
        await redis.connection_pool.disconnect()


app = FastAPI(
    title='tiny-instancer',
    description='https://github.com/es3n1n/tiny-instancer',
    version='1.0.0',
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(frontend_router)
app.include_router(instances_router)
