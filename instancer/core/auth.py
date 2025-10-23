from typing import Annotated

from fastapi import Depends

from instancer.auth_providers.abc import AuthProviderABC, InnerAuthSession
from instancer.auth_providers.local import LocalAuthProvider
from instancer.auth_providers.rctf import RCTFAuthProvider
from instancer.core.config import AuthProvider, config


def get_auth_provider() -> AuthProviderABC:
    if config.AUTH_PROVIDER == AuthProvider.LOCAL:
        return LocalAuthProvider(config.AUTH_PROVIDER_ARGS)
    if config.AUTH_PROVIDER == AuthProvider.RCTF:
        return RCTFAuthProvider(config.AUTH_PROVIDER_ARGS)

    msg = f'Unsupported AUTH_PROVIDER: {config.AUTH_PROVIDER}'
    raise ValueError(msg)


auth_provider = get_auth_provider()
AuthSession = Annotated[InnerAuthSession, Depends(auth_provider.authenticate)]
