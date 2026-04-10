"""Skill registry for Claude Code plugin skills.

Scans ~/.claude/plugins/cache/ for installed plugin skills by parsing
SKILL.md frontmatter, and provides sorted command lists for Telegram
bot menu registration.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ccbot.utils import atomic_write_json

logger = logging.getLogger(__name__)


@dataclass
class SkillInfo:
    """Metadata for a single Claude Code plugin skill."""

    name: str  # Original skill name (e.g. "systematic-debugging")
    command: str  # Telegram command (e.g. "systematic_debugging")
    description: str  # Short description for command menu (max 256 chars)
    plugin: str  # Parent plugin name (e.g. "superpowers")
    slash_command: str  # Command to send to Claude (e.g. "/systematic-debugging")


class SkillRegistry:
    """Scan and manage Claude Code plugin skills for Telegram bot integration."""

    def __init__(self, plugins_dir: Path, state_path: Path) -> None:
        self._plugins_dir = plugins_dir
        self._state_path = state_path
        self._skills: dict[str, SkillInfo] = {}
        self._state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        """Load persisted state from disk."""
        if self._state_path.exists():
            try:
                with open(self._state_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"favorites": [], "usage": {}}

    def _save_state(self) -> None:
        """Persist state to disk atomically."""
        atomic_write_json(self._state_path, self._state)

    def scan(self) -> list[SkillInfo]:
        """Scan plugins cache directory and return discovered skills.

        Produces CLI-compatible slash commands in the form /plugin:skill-dir-name
        (e.g. /superpowers:brainstorming, /octo:km). Telegram commands use
        underscores: superpowers_brainstorming, octo_km.
        """
        if not self._plugins_dir.is_dir():
            logger.warning("Plugins directory not found: %s", self._plugins_dir)
            return []

        skills: dict[str, SkillInfo] = {}
        for marketplace_dir in sorted(self._plugins_dir.iterdir()):
            if not marketplace_dir.is_dir():
                continue
            for plugin_dir in sorted(marketplace_dir.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                plugin_name = plugin_dir.name
                # Pick the latest version directory only
                version_dir = self._latest_version_dir(plugin_dir)
                if not version_dir:
                    continue
                skills_dir = version_dir / "skills"
                if not skills_dir.is_dir():
                    continue
                for skill_dir in sorted(skills_dir.iterdir()):
                    if not skill_dir.is_dir():
                        continue
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_md.is_file():
                        continue
                    _, description = self._parse_skill_md(skill_md)
                    if not description:
                        continue

                    # Use directory name as skill identifier (matches CLI behavior)
                    dir_name = skill_dir.name
                    # CLI slash command: /plugin:dir-name
                    slash_cmd = f"/{plugin_name}:{dir_name}"
                    # Telegram command: plugin_dirname (hyphens→underscores)
                    tg_cmd = self._to_command(f"{plugin_name}_{dir_name}")

                    if tg_cmd in skills:
                        # Skip duplicates (shouldn't happen with plugin prefix)
                        continue

                    skills[tg_cmd] = SkillInfo(
                        name=f"{plugin_name}:{dir_name}",
                        command=tg_cmd,
                        description=description[:256],
                        plugin=plugin_name,
                        slash_command=slash_cmd,
                    )

        self._skills = skills
        logger.info("Scanned %d skills from %s", len(skills), self._plugins_dir)
        return list(skills.values())

    @staticmethod
    def _latest_version_dir(plugin_dir: Path) -> Path | None:
        """Pick the latest version directory from a plugin package.

        Tries semver sorting first, falls back to lexicographic.
        Returns None if no valid directory found.
        """
        candidates = [d for d in plugin_dir.iterdir() if d.is_dir()]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        def version_key(d: Path) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in d.name.split("."))
            except ValueError:
                return (0,)

        return max(candidates, key=version_key)

    @staticmethod
    def _parse_skill_md(path: Path) -> tuple[str, str]:
        """Parse YAML frontmatter from SKILL.md for name and description."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ("", "")

        # Match YAML frontmatter between --- delimiters
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not match:
            return ("", "")

        frontmatter = match.group(1)
        name = ""
        description = ""
        for line in frontmatter.splitlines():
            line = line.strip()
            if line.startswith("name:"):
                name = line[5:].strip().strip("\"'")
            elif line.startswith("description:"):
                description = line[12:].strip().strip("\"'")

        return (name, description)

    @staticmethod
    def _to_command(name: str) -> str:
        """Convert skill name to Telegram command.

        Hyphens become underscores, lowercase, max 32 chars.
        """
        cmd = name.lower().replace("-", "_")
        cmd = re.sub(r"[^a-z0-9_]", "", cmd)
        return cmd[:32]

    def is_skill(self, command: str) -> bool:
        """Check if a command maps to a registered skill."""
        return command in self._skills

    def get_slash_command(self, command: str) -> str:
        """Get original slash command for a Telegram command."""
        info = self._skills.get(command)
        return info.slash_command if info else f"/{command}"

    def record_usage(self, command: str, project_dir: str | None) -> None:
        """Record skill usage for a project directory."""
        if project_dir is None:
            return
        usage: dict[str, dict[str, int]] = self._state.setdefault("usage", {})
        project_usage = usage.setdefault(project_dir, {})
        project_usage[command] = project_usage.get(command, 0) + 1
        self._save_state()

    def toggle_favorite(self, command: str) -> bool:
        """Toggle favorite status for a command. Returns new state."""
        favorites: list[str] = self._state.setdefault("favorites", [])
        if command in favorites:
            favorites.remove(command)
            self._save_state()
            return False
        favorites.append(command)
        self._save_state()
        return True

    def is_favorite(self, command: str) -> bool:
        """Check if a command is favorited."""
        return command in self._state.get("favorites", [])

    def get_sorted_skills(self, project_dir: str | None = None) -> list[SkillInfo]:
        """Get skills sorted by: favorites first, then usage count, then alpha."""
        skills = list(self._skills.values())
        favorites: list[str] = self._state.get("favorites", [])
        usage: dict[str, int] = {}
        if project_dir:
            usage = self._state.get("usage", {}).get(project_dir, {})

        def sort_key(s: SkillInfo) -> tuple[int, int, str]:
            is_fav = 0 if s.command in favorites else 1
            use_count = -(usage.get(s.command, 0))
            return (is_fav, use_count, s.command)

        return sorted(skills, key=sort_key)
