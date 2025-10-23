import http

import orjson
from fastapi import HTTPException, Request
from httpx import AsyncClient, HTTPError

from instancer.core.cache import cache_token, try_get_team_id_by_token

from .abc import AuthProviderABC, InnerAuthSession, extract_token


class RCTFAuthProvider(AuthProviderABC):
    def __init__(self, args: dict[str, str]) -> None:
        super().__init__(args)
        self.rctf_url = args.get('rctf_url')

        if not self.rctf_url:
            msg = 'rctf_url argument is required for RCTFAuthProvider'
            raise ValueError(msg)

        self.rctf_url = self.rctf_url.rstrip('/')

    async def authenticate(self, request: Request) -> InnerAuthSession:
        token = extract_token(request)
        if not token:
            raise HTTPException(status_code=401, detail='Authorization token is missing')

        cached = await try_get_team_id_by_token(token)
        if cached is not None:
            return InnerAuthSession(team_id=cached)

        async with AsyncClient() as client:
            try:
                r = await client.get(
                    f'{self.rctf_url}/api/v1/users/me',
                    headers={'Authorization': f'Bearer {token}'},
                )
            except HTTPError as err:
                raise HTTPException(
                    status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR.value, detail='Internal rCTF error'
                ) from err

            if r.status_code != http.HTTPStatus.OK.value:
                raise HTTPException(status_code=http.HTTPStatus.FORBIDDEN.value, detail='Invalid authorization token')

            resp_json = orjson.loads(r.content)
            kind = resp_json.get('kind')
            data = resp_json.get('data', {})
            if kind not in {'goodUserData', 'goodUserSelfData'}:
                raise HTTPException(status_code=http.HTTPStatus.FORBIDDEN.value, detail='Invalid authorization token')

            team_id = data.get('id')
            if not team_id:
                raise HTTPException(
                    status_code=http.HTTPStatus.FORBIDDEN.value, detail='No team ID associated with token'
                )

            await cache_token(token=token, team_id=str(team_id))
            return InnerAuthSession(team_id=str(team_id))
