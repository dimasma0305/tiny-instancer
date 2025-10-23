from fastapi import Request

from .abc import AuthProviderABC, InnerAuthSession


class LocalAuthProvider(AuthProviderABC):
    async def authenticate(self, request: Request) -> InnerAuthSession:  # noqa: ARG002
        return InnerAuthSession(team_id='local')
