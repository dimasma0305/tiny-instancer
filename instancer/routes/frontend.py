import http

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from instancer.core.challenges import get_challenge
from instancer.core.config import config


router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=config.TEMPLATES_PATH)


@router.get('/')
async def get_frontend_root() -> dict[str, str]:
    return {'detail': 'Use /challenges/<challenge_name> to access specific challenge.'}


@router.get('/auth')
async def get_frontend_auth(request: Request, state: str, token: str | None = None) -> HTMLResponse:
    if not config.AUTH_PLATFORM_URL:
        raise HTTPException(
            status_code=http.HTTPStatus.INTERNAL_SERVER_ERROR.value, detail='Auth platform url is not set'
        )

    challenge_item = get_challenge(state)
    return templates.TemplateResponse(
        request=request,
        name='auth.html',
        context={
            'challenge': challenge_item,
            'token': token,
            'auth_platform_url': config.AUTH_PLATFORM_URL,
        },
    )


@router.get('/challenges/{challenge_name}')
async def get_challenge_frontend(request: Request, challenge_name: str) -> HTMLResponse:
    challenge = get_challenge(challenge_name)
    return templates.TemplateResponse(
        request=request,
        name='index.html',
        context={
            'challenge': challenge,
            'hcaptcha_site_key': config.HCAPTCHA_SITE_KEY if config.is_hcaptcha_config_set else None,
        },
    )
