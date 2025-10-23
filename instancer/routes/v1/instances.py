from fastapi import APIRouter, Request

from instancer.core import instances
from instancer.core.auth import AuthSession
from instancer.util.hcaptcha import HCaptchaForm


router = APIRouter(
    prefix='/v1/instances',
    tags=['instances'],
)


@router.get('/{challenge_name}')
async def get_instance(
    challenge_name: str,
    session: AuthSession,
) -> instances.Instance:
    return await instances.get_instance(challenge_name, session.team_id)


@router.put('/{challenge_name}')
async def start_instance(
    request: Request, challenge_name: str, session: AuthSession, form: HCaptchaForm
) -> instances.Instance:
    await form.validate_captcha(request)
    return await instances.start_instance(challenge_name, session.team_id)


@router.delete('/{challenge_name}')
async def stop_instance(
    request: Request, challenge_name: str, session: AuthSession, form: HCaptchaForm
) -> instances.Instance:
    await form.validate_captcha(request)
    return await instances.stop_instance(challenge_name, session.team_id)
