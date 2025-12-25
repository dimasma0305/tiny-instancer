import os
import yaml
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from instancer.core.config import config
from instancer.util.logger import logger

def run_command(command: List[str], cwd: Optional[Path] = None) -> None:
    """
    Execute a shell command in a specified directory.

    Args:
        command: List of command arguments.
        cwd: Directory context for execution. Defaults to current working directory.
    
    Raises:
        subprocess.CalledProcessError: If the command returns a non-zero exit code.
    """
    logger.info(f"Running command: {' '.join(command)} in {cwd or os.getcwd()}")
    subprocess.check_call(command, cwd=str(cwd) if cwd else None)

def parse_compose(compose_path: Path, category: str, challenge_name: str) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """
    Parse a docker-compose.yml file to extract container configuration and image tags.

    Args:
        compose_path: Path to the docker-compose.yml file.
        category: Challenge category (e.g., 'web').
        challenge_name: Name of the challenge.

    Returns:
        A tuple containing:
        - List of container dictionaries with configuration.
        - List of tuples for image tagging (source_tag, target_tag).
    """
    with open(compose_path, 'r') as f:
        compose = yaml.safe_load(f)

    containers = []
    tags_to_apply = []
    services = compose.get('services', {})
    
    for service_name, service_config in services.items():
        container = {
            'name': service_name,
            'security': {}
        }

        if 'image' in service_config:
            container['image'] = service_config['image']
        elif 'build' in service_config:
            source_image = f"{challenge_name}-{service_name}:latest"
            target_image = f"{category}/{challenge_name}/{service_name}:latest"
            
            container['image'] = target_image
            tags_to_apply.append((source_image, target_image))
        else:
            continue
            
        deploy = service_config.get('deploy', {})
        resources = deploy.get('resources', {})
        limits = resources.get('limits', {})
        if limits:
            container['limits'] = {
                'memory': limits.get('memory', '128Mi'),
                'cpu': str(limits.get('cpus', '0.5'))
            }

        if 'read_only' in service_config:
             container['security']['read_only_fs'] = service_config['read_only']
        
        if 'cap_add' in service_config:
             container['security']['cap_add'] = service_config['cap_add']
        
        containers.append(container)
        
    return containers, tags_to_apply

def get_exposed_ports(compose_path: Path) -> List[Dict[str, Any]]:
    """
    Extract exposed ports from a docker-compose.yml file.

    Args:
        compose_path: Path to the docker-compose.yml file.

    Returns:
        List of dictionaries defining exposed ports with their protocol kind.
    """
    with open(compose_path, 'r') as f:
        compose = yaml.safe_load(f)
        
    exposed = []
    services = compose.get('services', {})
    
    for service_name, service_config in services.items():
        ports = service_config.get('ports', [])
        for port_mapping in ports:
            if isinstance(port_mapping, str):
                parts = port_mapping.split(':')
                container_port = int(parts[-1])
            elif isinstance(port_mapping, dict):
                 container_port = port_mapping.get('target', 80)
            else:
                continue

            kind = 'tcp'
       
            exposed.append({
                'kind': kind,
                'container_name': service_name,
                'container_port': container_port
            })
            
    return exposed


def process_challenge(challenge_path: Path, category: str, name: str) -> Optional[Dict[str, Any]]:
    """
    Process a single challenge directory to generate its configuration.

    Args:
        challenge_path: Path to the challenge.yml file.
        category: Challenge category.
        name: Challenge name.

    Returns:
        Dictionary containing the challenge configuration, or None if processing fails.
    """
    logger.info(f"Processing challenge: {category}/{name}")
    
    try:
        with open(challenge_path, 'r') as f:
            chal_config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to read {challenge_path}: {e}")
        return None

    dashboard = chal_config.get('dashboard')
    if not dashboard:
        dashboard = chal_config.get('extra', {}).get('dashboard', {})
        
    compose_rel_path = dashboard.get('config') or dashboard.get('path')
    
    if not compose_rel_path:
        return None
        
    challenge_dir = challenge_path.parent
    abs_compose_path = (challenge_dir / compose_rel_path).resolve()
    
    if not abs_compose_path.exists():
        logger.error(f"Compose file not found: {abs_compose_path}")
        return None

    try:
        run_command(['docker', 'compose', '-p', name, '-f', str(abs_compose_path), 'build'], cwd=challenge_dir)
    except Exception as e:
        logger.error(f"Failed to build challenge {name}: {e}")
        return None

    containers, tags = parse_compose(abs_compose_path, category, name)
    expose = get_exposed_ports(abs_compose_path)
    
    for src, dst in tags:
        try:
            run_command(['docker', 'tag', src, dst])
        except Exception as e:
            logger.error(f"Failed to tag {src} as {dst}: {e}")

    return {
        'name': name,
        'timeout': dashboard.get('timeout', chal_config.get('timeout', 900)),
        'containers': containers,
        'expose': expose
    }


def build_all_challenges() -> None:
    """
    Scan the challenges directory, process all valid challenges, and generate the master configuration file.
    """
    from instancer.util.fs import ROOT_DIR
    
    challenges_dir = ROOT_DIR / 'challenges'
    if not challenges_dir.exists():
        logger.warning(f"Challenges directory not found: {challenges_dir}")
        return

    challenges_list = []
    
    for category_dir in challenges_dir.iterdir():
        if not category_dir.is_dir():
            continue
            
        for challenge_dir in category_dir.iterdir():
            if not challenge_dir.is_dir():
                continue
                
            c_yaml = challenge_dir / 'challenge.yml'
            if not c_yaml.exists():
                c_yaml = challenge_dir / 'challenge.yaml'
            
            if c_yaml.exists():
                data = process_challenge(c_yaml, category_dir.name, challenge_dir.name)
                if data:
                    challenges_list.append(data)

    output_path = config.CHALLENGES_YAML_PATH
    if not Path(output_path).is_absolute():
        output_path = ROOT_DIR / output_path
        
    logger.info(f"Writing {len(challenges_list)} challenges to {output_path}")
    
    with open(output_path, 'w') as f:
        f.write("# Generated by instancer.builder\n")
        
        for i, chal in enumerate(challenges_list):
            yaml.dump(chal, f, default_flow_style=False, sort_keys=False)
            if i < len(challenges_list) - 1:
                f.write("---\n")

