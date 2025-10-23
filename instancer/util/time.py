from datetime import UTC, datetime


def timestamp() -> int:
    return int(datetime.now(tz=UTC).timestamp())
