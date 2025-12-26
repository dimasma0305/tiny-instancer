import re
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from instancer.core.config import config
from instancer.util.logger import logger


NANO_CPU_SCALE = 1_000_000_000


class ExposeKind(StrEnum):
    HTTPS = 'https'
    HTTP = 'http'
    TCP = 'tcp'


def expose_kind_to_port(kind: ExposeKind) -> int:
    return {
        ExposeKind.HTTP: config.TRAEFIK_HTTP_PORT,
        ExposeKind.HTTPS: config.TRAEFIK_HTTPS_PORT,
        ExposeKind.TCP: config.TRAEFIK_TCP_PORT,
    }[kind]


def require_valid_name(name: str) -> None:
    if not re.fullmatch(r'[a-z0-9-]+', name):
        msg = f'Name "{name}" is invalid. It must match [a-z0-9-]+.'
        raise ValueError(msg)


class Container(BaseModel):
    class Security(BaseModel):
        read_only_fs: bool = True
        security_opt: list[str] = Field(default_factory=lambda: ['no-new-privileges'])
        cap_add: list[str] = Field(default_factory=list)
        cap_drop: list[str] = Field(default_factory=lambda: ['ALL'])

    class Limits(BaseModel):
        class Ulimit(BaseModel):
            name: str
            soft: int
            hard: int

        memory: str = '512m'
        cpu: str = '0.5'
        pids_limit: int = 1024
        ulimits: list[Ulimit] = Field(
            default_factory=lambda: [
                Container.Limits.Ulimit(name='nofile', soft=1024, hard=1024),
            ]
        )

        _memory_bytes: int | None = None
        _nano_cpus: int | None = None

        @property
        def nano_cpus(self) -> int:
            if self._nano_cpus is not None:
                return self._nano_cpus

            if not self.cpu:
                self._nano_cpus = 0
                return self._nano_cpus

            if self.cpu.endswith('m'):
                millicores = int(self.cpu[:-1])
                self._nano_cpus = (millicores * NANO_CPU_SCALE) // 1000
                return self._nano_cpus

            cores = float(self.cpu)
            self._nano_cpus = int(cores * NANO_CPU_SCALE)
            return self._nano_cpus

        @property
        def memory_bytes(self) -> int:
            if self._memory_bytes is not None:
                return self._memory_bytes

            suffixes = {
                'b': 1,
                'k': 1024,
                'kb': 1024,
                'ki': 1024,
                'm': 1024**2,
                'mb': 1024**2,
                'mi': 1024**2,
                'g': 1024**3,
                'gb': 1024**3,
                'gi': 1024**3,
                't': 1024**4,
                'tb': 1024**4,
            }
            mem = self.memory.lower()
            for suffix, multiplier in suffixes.items():
                if mem.endswith(suffix):
                    number = float(mem[: -len(suffix)])
                    self._memory_bytes = int(number * multiplier)
                    return self._memory_bytes

            self._memory_bytes = int(mem)
            return self._memory_bytes

    name: str
    image: str
    env: dict[str, str] = Field(default_factory=dict)
    egress: bool = False
    security: Security = Field(default_factory=Security)
    limits: Limits = Field(default_factory=Limits)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        require_valid_name(v)
        return v

    @model_validator(mode='after')
    def validate_model(self) -> Self:
        if not self.security.read_only_fs:
            logger.warning(f'Container "{self.name}" has read_only_fs set to False.')

        if not self.security.security_opt:
            logger.warning(f'Container "{self.name}" has empty security_opt list.')

        for val, name in (
            (self.limits.memory_bytes, 'memory'),
            (self.limits.nano_cpus, 'cpu'),
            (self.limits.pids_limit, 'pids_limit'),
        ):
            if val <= 0:
                logger.warning(f'Container "{self.name}" has non-positive {name} limit.')

        return self


class Expose(BaseModel):
    kind: ExposeKind
    container_name: str
    container_port: int


class Challenge(BaseModel):
    name: str
    timeout: int
    containers: list[Container]
    expose: list[Expose]

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        require_valid_name(v)
        return v

    @model_validator(mode='after')
    def validate_model(self) -> Self:
        container_names = {container.name for container in self.containers}
        for expose in self.expose:
            if expose.container_name in container_names:
                continue

            msg = f'Expose references unknown container "{expose.container_name}" in challenge "{self.name}".'
            raise ValueError(msg)

        return self


def load_challenges() -> dict[str, Challenge]:
    result: dict[str, Challenge] = {}
    path = Path(config.CHALLENGES_YAML_PATH)

    files_to_read = []
    if path.is_file():
        files_to_read.append(path)
    elif path.is_dir():
        # Search recursively for challenge config files
        files_to_read.extend(path.rglob('challenge.yml'))
        files_to_read.extend(path.rglob('challenge.yaml'))
    
    # Deduplicate
    files_to_read = list(set(files_to_read))
    
    if not files_to_read:
        logger.warning(f"No challenge configuration files found in {path}")

    for file_path in files_to_read:
        logger.info(f"Loading challenges from {file_path}")
        try:
            content = file_path.read_bytes()
            for item in yaml.safe_load_all(content):
                if item:
                    try:
                        challenge = TypeAdapter(Challenge).validate_python(item)
                        result[challenge.name] = challenge
                        logger.info(f"Loaded challenge '{challenge.name}' from {file_path}")
                    except Exception as ve:
                        # Log validation error but keep going for other docs
                        logger.error(f"Validation error in {file_path}: {ve}")
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")

    logger.info(f'Loaded {len(result)} challenges from {len(files_to_read)} files.')
    return result


challenges = load_challenges()


def get_challenge(challenge_name: str) -> Challenge:
    result = challenges.get(challenge_name)
    if not result:
        raise HTTPException(status_code=404, detail='Challenge not found')
    return result
