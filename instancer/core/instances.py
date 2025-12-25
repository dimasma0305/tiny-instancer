import asyncio
import http
import uuid
from asyncio import sleep
from enum import StrEnum
from functools import cache

from aiodocker import Docker, DockerError
from aiodocker.containers import DockerContainer
from fastapi import HTTPException
from pydantic import BaseModel

from instancer.core.cache import instance_lock
from instancer.core.challenges import Challenge, Container, ExposeKind, expose_kind_to_port, get_challenge
from instancer.core.config import config
from instancer.util.logger import logger
from instancer.util.time import timestamp


NOT_ACQUIRED_ERROR = HTTPException(status_code=400, detail='Another instance operation is in progress.')


class InstanceStatus(StrEnum):
    STOPPED = 'stopped'
    RUNNING = 'running'
    STARTING = 'starting'


class Instance(BaseModel):
    class Endpoint(BaseModel):
        kind: ExposeKind
        host: str
        port: int

    status: InstanceStatus
    timeout: int
    endpoints: list[Endpoint] | None = None
    remaining_time: int | None = None


class ContainerLabels(StrEnum):
    MANAGED_BY = 'io.es3n1n.managed_by'
    CHALLENGE = 'io.es3n1n.instancer.challenge'
    TEAM_ID = 'io.es3n1n.instancer.team_id'
    TARGET_HOSTNAME = 'io.es3n1n.instancer.hostname'
    INSTANCE_ID = 'io.es3n1n.instancer.instance_id'
    STARTED_AT = 'io.es3n1n.instancer.started_at'
    EXPIRES_AT = 'io.es3n1n.instancer.expires_at'


@cache
def get_docker() -> Docker:
    # FIXME(es3n1n): This is a workaround for prunner process not having a loop at import time.
    return Docker()


def _get_search_filters(challenge_name: str, team_id: str) -> dict[str, dict | list | str]:
    return {
        'label': [
            f'{ContainerLabels.MANAGED_BY}={config.DOCKER_MANAGER_NAME}',
            f'{ContainerLabels.CHALLENGE}={challenge_name}',
            f'{ContainerLabels.TEAM_ID}={team_id}',
        ]
    }


async def get_containers(
    challenge_name: str,
    team_id: str,
    *,
    limit: int | None = None,
    running_only: bool = False,
) -> list[DockerContainer]:
    kwargs: dict[str, int] = {}
    if limit is not None:
        kwargs['limit'] = limit
    try:
        return await get_docker().containers.list(
            all=not running_only, filters=_get_search_filters(challenge_name, team_id), **kwargs
        )
    except DockerError as err:
        logger.opt(exception=err).error(f'Error getting containers: {challenge_name=} {team_id=}')
        return []


async def is_running(challenge_name: str, team_id: str) -> bool:
    return bool(await get_containers(challenge_name, team_id, running_only=True, limit=1))


async def _ensure_network(name: str, *, internal: bool, expires_at: int) -> None:
    try:
        network = await get_docker().networks.get(name)
    except DockerError:
        try:
            network = await get_docker().networks.create(
                {
                    'Name': name,
                    'Driver': 'bridge',
                    'Internal': internal,
                    'Labels': {
                        ContainerLabels.MANAGED_BY: config.DOCKER_MANAGER_NAME,
                        ContainerLabels.EXPIRES_AT: str(expires_at),
                    },
                }
            )
        except DockerError as err:
            if err.status == http.HTTPStatus.BAD_REQUEST.value and 'fully subnetted' in (err.message or ''):
                raise HTTPException(
                    status_code=500,
                    detail='Daemon has run out of available subnets for creating networks. Contact admins.',
                ) from err
            raise

    # Connect traefik if internal
    if internal:
        try:
            await network.connect(
                {
                    'Container': config.TRAEFIK_CONTAINER_NAME,
                }
            )
        except DockerError as err:
            if err.status != http.HTTPStatus.CONFLICT.value:
                raise


def _add_expose_labels(
    host: str,
    labels: dict[str, str],
    challenge: Challenge,
    container: Container,
    team_id: str,
    instance_id: str,
) -> None:
    for i, expose in enumerate(challenge.expose):
        if expose.container_name != container.name:
            continue

        router_name = f'{config.PREFIX}-{challenge.name}-{team_id}-{instance_id}-{container.name}-{i}'

        match expose.kind:
            case ExposeKind.TCP:
                labels[f'traefik.tcp.routers.{router_name}.rule'] = f'HostSNI(`{host}`)'
                labels[f'traefik.tcp.routers.{router_name}.entrypoints'] = config.TRAEFIK_TCP_ENTRYPOINT
                labels[f'traefik.tcp.routers.{router_name}.service'] = router_name
                labels[f'traefik.tcp.routers.{router_name}.tls.passthrough'] = 'true'
                labels[f'traefik.tcp.services.{router_name}.loadbalancer.server.port'] = str(expose.container_port)

            case ExposeKind.HTTP:
                labels[f'traefik.http.routers.{router_name}.rule'] = f'Host(`{host}`)'
                labels[f'traefik.http.routers.{router_name}.entrypoints'] = config.TRAEFIK_HTTP_ENTRYPOINT
                labels[f'traefik.http.routers.{router_name}.service'] = router_name
                labels[f'traefik.http.services.{router_name}.loadbalancer.server.port'] = str(expose.container_port)

            case ExposeKind.HTTPS:
                labels[f'traefik.http.routers.{router_name}.rule'] = f'Host(`{host}`)'
                labels[f'traefik.http.routers.{router_name}.entrypoints'] = config.TRAEFIK_HTTPS_ENTRYPOINT
                labels[f'traefik.http.routers.{router_name}.tls'] = 'true'
                labels[f'traefik.http.routers.{router_name}.service'] = router_name
                labels[f'traefik.http.services.{router_name}.loadbalancer.server.port'] = str(expose.container_port)


def _get_endpoints(challenge: Challenge, host: str | None) -> list[Instance.Endpoint] | None:
    return (
        [
            Instance.Endpoint(
                kind=expose.kind,
                host=host,
                port=expose_kind_to_port(expose.kind),
            )
            for expose in challenge.expose
        ]
        if host
        else None
    )


async def _cleanup_containers(containers: list[tuple[str, DockerContainer]]) -> None:
    if not containers:
        return

    delete_coroutines: list = []
    names: list[str] = []
    for name, container in containers:
        delete_coroutines.append(container.delete(force=True))
        names.append(name)

    await asyncio.gather(*delete_coroutines)


async def _cleanup_networks(names: list[str]) -> None:
    if not names:
        return

    delete_coroutines: list = []
    existing_names: list[str] = []
    for name in names:
        try:
            network = await get_docker().networks.get(name)
        except DockerError as err:
            if err.status != http.HTTPStatus.NOT_FOUND.value:
                logger.opt(exception=err).warning(f'Failed to fetch network during rollback cleanup: {name}')
            continue

        details = await network.show()
        for conn in (details['Containers'] or {}).values():
            logger.info(f'Disconnecting container {conn["Name"]} from network {name} during rollback cleanup')
            await network.disconnect(
                {
                    'Container': conn['Name'],
                    'Force': True,
                },
            )

        delete_coroutines.append(network.delete())
        existing_names.append(name)

    if not delete_coroutines:
        return

    await asyncio.gather(*delete_coroutines)


async def start_instance(challenge_name: str, team_id: str) -> Instance:
    challenge = get_challenge(challenge_name)
    async with instance_lock(challenge_name, team_id) as acquired:
        if not acquired:
            raise NOT_ACQUIRED_ERROR

        if await is_running(challenge_name, team_id):
            raise HTTPException(status_code=400, detail='Instance is already running')

        started_at = timestamp()
        expires_at = started_at + challenge.timeout

        instance_id = uuid.uuid4().hex[:12]
        host = f'{challenge.name}-{instance_id}.{config.INSTANCES_HOST}'

        svc_net = f'{config.PREFIX}-svc-{challenge_name}-{team_id}-{instance_id}'
        eg_net = f'{config.PREFIX}-eg-{challenge_name}-{team_id}-{instance_id}'

        created_containers: list[tuple[str, DockerContainer]] = []
        networks_created: list[str] = []

        try:
            await _ensure_network(svc_net, internal=True, expires_at=expires_at)
            networks_created.append(svc_net)

            if any(c.egress for c in challenge.containers):
                await _ensure_network(eg_net, internal=False, expires_at=expires_at)
                networks_created.append(eg_net)

            for container in challenge.containers:
                try:
                    await get_docker().images.get(container.image)
                except DockerError:
                    await get_docker().images.pull(container.image)

                labels: dict[str, str] = {
                    ContainerLabels.MANAGED_BY: config.DOCKER_MANAGER_NAME,
                    ContainerLabels.CHALLENGE: challenge_name,
                    ContainerLabels.TEAM_ID: team_id,
                    ContainerLabels.TARGET_HOSTNAME: host,
                    ContainerLabels.STARTED_AT: str(started_at),
                    ContainerLabels.EXPIRES_AT: str(expires_at),
                    ContainerLabels.INSTANCE_ID: instance_id,
                }

                if challenge.expose:
                    labels['traefik.enable'] = 'true'
                    labels['traefik.docker.network'] = svc_net
                _add_expose_labels(host, labels, challenge, container, team_id, instance_id)

                # Setup networking
                endpoints_config: dict[str, dict] = {
                    svc_net: {},
                }
                if container.egress:
                    endpoints_config[eg_net] = {}

                container_name = f'{config.PREFIX}-{challenge_name}-{team_id}-{container.name}'
                logger.info(f'Spinning up container {container_name=} {challenge_name=} {team_id=}')
                created_container = await get_docker().containers.create(
                    config={
                        'Hostname': container.name,
                        'Image': container.image,
                        'Env': [f'{k}={v}' for k, v in container.env.items()],
                        'Labels': labels,
                        'HostConfig': {
                            'RestartPolicy': {
                                'Name': 'unless-stopped',
                            },
                            'ReadOnlyRootfs': container.security.read_only_fs,
                            'Tmpfs': {'/tmp': 'noexec,nosuid,nodev'} if container.security.read_only_fs else {},  # noqa: S108
                            'SecurityOpt': container.security.security_opt,
                            'Memory': container.limits.memory_bytes,
                            'MemorySwap': container.limits.memory_bytes,
                            'NanoCpus': container.limits.nano_cpus,
                            'PidsLimit': container.limits.pids_limit,
                            'CapAdd': container.security.cap_add,
                            'CapDrop': container.security.cap_drop,
                            'LogConfig': {
                                'Type': 'json-file',
                            },
                            'Ulimits': [
                                {'Name': ulimit.name, 'Soft': ulimit.soft, 'Hard': ulimit.hard}
                                for ulimit in container.limits.ulimits
                            ],
                        },
                        'NetworkingConfig': {
                            'EndpointsConfig': endpoints_config,
                        },
                    },
                    name=container_name,
                )
                created_containers.append((container_name, created_container))

            start_tasks = [container.start() for _, container in created_containers]
            await asyncio.gather(*start_tasks)
        except Exception as err:
            await _cleanup_containers(created_containers)
            await _cleanup_networks(networks_created)

            if isinstance(err, HTTPException):
                raise

            logger.opt(exception=err).error(f'Failed to start instance: {challenge_name=} {team_id=}')
            raise HTTPException(status_code=500, detail='Failed to start instance') from err

        return Instance(
            status=InstanceStatus.STARTING,
            timeout=challenge.timeout,
            endpoints=_get_endpoints(challenge, host),
            remaining_time=expires_at - timestamp(),
        )


async def stop_instance(challenge_name: str, team_id: str) -> Instance:
    async with instance_lock(challenge_name, team_id) as acquired:
        if not acquired:
            raise NOT_ACQUIRED_ERROR

        instance_containers = await get_containers(challenge_name, team_id)
        if not instance_containers:
            raise HTTPException(status_code=404, detail='Instance not found')

        networks_to_remove: set[str] = set()
        stop_tasks = []

        for container in instance_containers:
            details = await container.show()
            for net_name in details['NetworkSettings']['Networks']:
                # Remove only our stuff
                if not net_name.startswith(f'{config.PREFIX}-'):
                    continue

                networks_to_remove.add(net_name)

            logger.info(f'Stopping container {container.id=} {challenge_name=} {team_id=}')
            stop_tasks.append(container.stop(t=config.DOCKER_STOP_TIMEOUT_SECONDS))

        await asyncio.gather(*stop_tasks, return_exceptions=True)
        await asyncio.gather(*[c.delete(force=True) for c in instance_containers], return_exceptions=True)
        logger.info(f'Removed {len(instance_containers)} containers.')

        net_remove_tasks = []
        net_disconnect_tasks = []
        for net_name in networks_to_remove:
            network = await get_docker().networks.get(net_name)

            # Disconnect everyone.
            # Doing show for the second time to reflect changes after container deletions.
            details = await network.show()
            for conn in (details['Containers'] or {}).values():
                logger.info(f'Disconnecting container {conn["Name"]} from network {net_name}')
                net_disconnect_tasks.append(
                    network.disconnect(
                        {
                            'Container': conn['Name'],
                            'Force': True,
                        },
                    )
                )

            logger.info(f'Removing network {net_name}')
            net_remove_tasks.append(network.delete())

        await asyncio.gather(*net_disconnect_tasks, return_exceptions=True)
        await asyncio.gather(*net_remove_tasks, return_exceptions=True)
        logger.info(f'Removed {len(networks_to_remove)} networks.')
        return Instance(
            status=InstanceStatus.STOPPED,
            timeout=get_challenge(challenge_name).timeout,
            endpoints=None,
            remaining_time=None,
        )


async def get_instance(challenge_name: str, team_id: str) -> Instance:
    containers = await get_containers(challenge_name, team_id, limit=1)

    status = InstanceStatus.STOPPED
    expires_at: int | None = None
    host: str | None = None
    if containers:
        details = await containers[0].show()
        labels = details['Config']['Labels']
        state = details['State']['Status']

        expires_at = int(labels[ContainerLabels.EXPIRES_AT])
        host = labels[ContainerLabels.TARGET_HOSTNAME]
        status = InstanceStatus.RUNNING if state == 'running' else InstanceStatus.STARTING

    challenge = get_challenge(challenge_name)
    return Instance(
        status=status,
        timeout=challenge.timeout,
        endpoints=_get_endpoints(challenge, host),
        remaining_time=max(0, expires_at - timestamp()) if expires_at else None,
    )


async def _prune_instances(docker: Docker, now: int) -> None:
    # TODO(es3n1n): Is there a way how to query containers by label value comparison?
    containers = await docker.containers.list(
        all=True,
        filters={
            'label': [
                f'{ContainerLabels.MANAGED_BY}={config.DOCKER_MANAGER_NAME}',
            ],
        },
    )

    for container in containers:
        try:
            details = await container.show()
        except DockerError:
            # Got deleted already
            continue
        labels = details['Config']['Labels']

        expires_at = int(labels[ContainerLabels.EXPIRES_AT])
        if expires_at > now:
            continue

        challenge = labels[ContainerLabels.CHALLENGE]
        team_id = labels[ContainerLabels.TEAM_ID]
        logger.info(f'Prunner stopping expired container {container.id=} {challenge=} {team_id=} {expires_at=} {now=}')

        try:
            await stop_instance(challenge, team_id)
        except HTTPException as err:
            logger.opt(exception=err).warning(
                f'Prunner failed to stop expired container {container.id=} via stop_instance, will try again'
            )
        except DockerError as err:
            logger.opt(exception=err).warning(f'Prunner failed to remove expired container {container.id=}')


async def _prune_networks(docker: Docker, now: int) -> None:
    # TODO(es3n1n): Is there a way how to query containers by label value comparison?
    networks = await docker.networks.list(
        filters={
            'label': [
                f'{ContainerLabels.MANAGED_BY}={config.DOCKER_MANAGER_NAME}',
            ],
        }
    )

    names_to_prune: list[str] = []
    for network in networks:
        # NOTE(es3n1n): Going for a private method as I dont want to do the inspect request 2 times / network
        details = await docker._query_json(f'networks/{network["Id"]}', method='GET')  # noqa: SLF001
        labels = details['Labels']

        expires_at = int(labels[ContainerLabels.EXPIRES_AT])
        if expires_at > now:
            continue

        logger.info(f'Prunning expired network {network["Name"]=} {expires_at=} {now=}')
        names_to_prune.append(network['Name'])

    await _cleanup_networks(names_to_prune)


async def instance_prunner() -> None:
    docker = get_docker()
    while True:
        now = timestamp()
        logger.info('Running instance prunner')
        try:
            await _prune_instances(docker, now)
            await _prune_networks(docker, now)
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=e).error('Encountered an error while prunning')
        await sleep(config.PRUNNER_INTERVAL_SECONDS)
