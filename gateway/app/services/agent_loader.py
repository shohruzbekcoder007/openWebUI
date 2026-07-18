"""Load and manage Hermes Agents from YAML/JSON configuration."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.models.agent import HermesAgent
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Matches ${VAR} or ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} placeholders in strings."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(var_name)
        if env_val is not None and env_val != "":
            return env_val
        if default is not None:
            return default
        return ""

    return _ENV_PATTERN.sub(replacer, value)


def _expand_recursive(obj: Any) -> Any:
    """Recursively expand env placeholders in nested structures."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, list):
        return [_expand_recursive(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _expand_recursive(v) for k, v in obj.items()}
    return obj


class AgentRegistry:
    """In-memory registry of Hermes Agents loaded from config."""

    def __init__(self) -> None:
        self._agents: Dict[str, HermesAgent] = {}
        self._by_name: Dict[str, str] = {}  # lowercase name -> id
        self._config_path: Optional[Path] = None

    @property
    def agents(self) -> Dict[str, HermesAgent]:
        return self._agents

    def load(self, config_path: str | Path) -> int:
        """Load agents from a YAML or JSON file.

        Returns the number of agents loaded (including disabled).
        """
        path = Path(config_path)
        self._config_path = path

        if not path.exists():
            logger.error("agents_config_missing", path=str(path))
            raise FileNotFoundError(f"Agents config not found: {path}")

        raw = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()

        if suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(raw) or {}
        elif suffix == ".json":
            data = json.loads(raw)
        else:
            # Try YAML first, then JSON
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                data = json.loads(raw)

        data = _expand_recursive(data)
        defaults: Dict[str, Any] = data.get("defaults") or {}
        agents_raw: List[Dict[str, Any]] = data.get("agents") or []

        if not agents_raw:
            logger.warning("agents_config_empty", path=str(path))

        new_agents: Dict[str, HermesAgent] = {}
        new_by_name: Dict[str, str] = {}

        for entry in agents_raw:
            merged = {**defaults, **entry}
            # Empty api_key string -> None
            if merged.get("api_key") == "":
                merged["api_key"] = None
            try:
                agent = HermesAgent.model_validate(merged)
            except Exception as exc:
                logger.error(
                    "agent_validation_failed",
                    agent_id=entry.get("id"),
                    error=str(exc),
                )
                raise

            if agent.id in new_agents:
                raise ValueError(f"Duplicate agent id: {agent.id}")

            new_agents[agent.id] = agent
            new_by_name[agent.name.lower()] = agent.id
            # Also allow selecting by id case-insensitively via name map
            new_by_name[agent.id.lower()] = agent.id

            logger.info(
                "agent_loaded",
                id=agent.id,
                name=agent.name,
                enabled=agent.enabled,
                base_url=agent.base_url,
            )

        self._agents = new_agents
        self._by_name = new_by_name
        logger.info("agents_registry_ready", count=len(new_agents), path=str(path))
        return len(new_agents)

    def reload(self) -> int:
        """Reload from the last known config path."""
        if not self._config_path:
            raise RuntimeError("No config path set; call load() first")
        return self.load(self._config_path)

    def get(self, agent_id: str) -> Optional[HermesAgent]:
        """Get agent by id."""
        return self._agents.get(agent_id)

    def resolve(self, model: str) -> Optional[HermesAgent]:
        """Resolve Open WebUI model string to an agent.

        Accepts agent id, display name, or `hermes/<id>` style ids.
        """
        if not model:
            return None

        key = model.strip()
        # Strip common prefixes
        for prefix in ("hermes/", "agent/", "openai/"):
            if key.lower().startswith(prefix):
                key = key[len(prefix) :]

        # Direct id
        agent = self._agents.get(key)
        if agent:
            return agent

        # Case-insensitive id / name
        mapped = self._by_name.get(key.lower())
        if mapped:
            return self._agents.get(mapped)

        return None

    def list_enabled(self) -> List[HermesAgent]:
        return [a for a in self._agents.values() if a.enabled]

    def list_all(self) -> List[HermesAgent]:
        return list(self._agents.values())

    def count_enabled(self) -> int:
        return sum(1 for a in self._agents.values() if a.enabled)


# Process-wide registry
registry = AgentRegistry()
