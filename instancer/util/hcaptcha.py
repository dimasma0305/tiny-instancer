import orjson
from fastapi import HTTPException, Request
from httpx import AsyncClient, HTTPError
from pydantic import BaseModel

from instancer.core.config import config
from instancer.util.logger import logger


class HCaptchaForm(BaseModel):
    captcha: str | None = None

    async def validate_captcha(self, request: Request | None = None) -> None:
        if not config.is_hcaptcha_config_set:
            return

        if self.captcha is None:
            raise HTTPException(status_code=400, detail='Captcha response is missing')

        remote_ip: str | None = None
        if request is not None and request.client is not None:
            remote_ip = request.client.host

        is_valid = await verify_hcaptcha(self.captcha, remote_ip)
        if not is_valid:
            raise HTTPException(status_code=400, detail='Captcha validation failed')


async def verify_hcaptcha(response: str, remote_ip: str | None = None) -> bool:
    if not config.HCAPTCHA_SECRET:
        logger.error('HCaptcha secret is not set!')
        return False

    data = {
        'secret': config.HCAPTCHA_SECRET.get_secret_value(),
        'response': response,
    }

    if remote_ip is not None:
        data['remoteip'] = remote_ip

    try:
        async with AsyncClient() as client:
            r = await client.post(
                'https://hcaptcha.com/siteverify',
                data=data,
            )
            r.raise_for_status()
            api_response = orjson.loads(r.content)
    except HTTPError as err:
        raise HTTPException(status_code=500, detail='Internal hcaptcha error') from err

    return api_response.get('success', False)
