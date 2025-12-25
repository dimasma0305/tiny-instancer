import jwt
from fastapi import HTTPException, Request

from .abc import AuthProviderABC, InnerAuthSession, extract_token


class CTFdAuthProvider(AuthProviderABC):
    def __init__(self, args: dict[str, str]) -> None:
        super().__init__(args)
        self.secret = args.get('secret')

        if not self.secret:
            msg = 'secret argument is required for CTFdAuthProvider'
            raise ValueError(msg)

    async def authenticate(self, request: Request) -> InnerAuthSession:
        token = extract_token(request)
        if not token:
            raise HTTPException(status_code=401, detail='Authorization token is missing')

        try:
            payload = jwt.decode(token, self.secret, algorithms=['HS256'])
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=403, detail='Invalid authorization token')

        team_id = payload.get('team_id')
        if not team_id:
            raise HTTPException(status_code=403, detail='Token missing team_id')

        return InnerAuthSession(team_id=str(team_id))
