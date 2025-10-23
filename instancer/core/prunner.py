from contextlib import suppress


try:
    from uvloop import run  # type: ignore[import-not-found]
except ImportError:
    from asyncio import run
from instancer.core.instances import instance_prunner


def prunner_process() -> None:
    with suppress(KeyboardInterrupt):
        run(instance_prunner())
