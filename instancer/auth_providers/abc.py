from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from fastapi import Request


@dataclass(frozen=True)
class InnerAuthSession:
    team_id: str


class AuthProviderABC(ABC):
    def __init__(self, args: dict[str, str]) -> None:
        self._args = args

    @abstractmethod
    async def authenticate(self, request: Request) -> InnerAuthSession:
        """Authenticate a user based on the incoming request."""


def extract_token(request: Request) -> str | None:
    # FIXME(es3n1n): this ideally should be using fastapi's dependency injection
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None

    parts = auth_header.split(' ')
    if len(parts) != 2 or parts[0].lower() != 'bearer':  # noqa: PLR2004
        return None

    return parts[1]
