# ccbot 스킬 메뉴 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 텔레그램 `/` 커맨드 메뉴에 설치된 Claude Code 플러그인 스킬을 자동 등록하여 탭 한 번으로 실행할 수 있게 한다.

**Architecture:** ccbot 시작 시 `~/.claude/plugins/cache/`를 스캔하여 SKILL.md에서 name/description을 파싱하고, 기존 봇 명령과 합쳐 `bot.set_my_commands()`로 등록한다. 즐겨찾기와 프로젝트별 사용빈도를 `skill_state.json`에 기록하여 커맨드 순서를 동적으로 정렬한다.

**Tech Stack:** Python 3.12, python-telegram-bot, pathlib, PyYAML frontmatter 파싱 (직접 구현, 의존성 추가 없음)

**Spec:** `docs/specs/2026-04-08-skill-menu-design.md`

---

### Task 1: SkillRegistry 모듈 — 스킬 스캔 및 파싱

**Files:**
- Create: `src/ccbot/skill_registry.py`
- Test: `tests/ccbot/test_skill_registry.py`

- [ ] **Step 1: 테스트 파일 생성 — 스킬 스캔 테스트**

```python
# tests/ccbot/test_skill_registry.py
"""Tests for skill_registry — plugin skill scanning and command registration."""

import json
from pathlib import Path

import pytest

from ccbot.skill_registry import SkillInfo, SkillRegistry


@pytest.fixture
def plugins_dir(tmp_path: Path) -> Path:
    """Create a fake plugins cache with SKILL.md files."""
    # superpowers plugin with two skills
    sp_dir = tmp_path / "claude-plugins-official" / "superpowers" / "5.0.7" / "skills"

    brainstorm_dir = sp_dir / "brainstorming"
    brainstorm_dir.mkdir(parents=True)
    (brainstorm_dir / "SKILL.md").write_text(
        "---\n"
        "name: brainstorming\n"
        'description: "Design features through collaborative dialogue"\n'
        "---\n\n# Brainstorming\n"
    )

    debug_dir = sp_dir / "systematic-debugging"
    debug_dir.mkdir(parents=True)
    (debug_dir / "SKILL.md").write_text(
        "---\n"
        "name: systematic-debugging\n"
        'description: "Debug issues systematically"\n'
        "---\n\n# Debugging\n"
    )

    # pr-review-toolkit plugin
    pr_dir = tmp_path / "claude-plugins-official" / "pr-review-toolkit" / "1.0.0" / "skills"
    cr_dir = pr_dir / "code-reviewer"
    cr_dir.mkdir(parents=True)
    (cr_dir / "SKILL.md").write_text(
        "---\n"
        "name: code-reviewer\n"
        'description: "Review code for quality and security"\n'
        "---\n\n# Code Reviewer\n"
    )

    return tmp_path


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "skill_state.json"


def test_scan_finds_all_skills(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    skills = registry.scan()
    assert len(skills) == 3
    names = {s.name for s in skills}
    assert names == {"brainstorming", "systematic-debugging", "code-reviewer"}


def test_command_name_converts_hyphens(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()
    commands = {s.command for s in registry.skills}
    assert "systematic_debugging" in commands
    assert "code_reviewer" in commands
    assert "brainstorming" in commands


def test_slash_command_preserves_original(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()
    debug_skill = next(s for s in registry.skills if s.name == "systematic-debugging")
    assert debug_skill.slash_command == "/systematic-debugging"


def test_scan_skips_non_skill_dirs(plugins_dir: Path, state_path: Path) -> None:
    # Add a commands/ directory (deprecated, should be skipped)
    cmd_dir = plugins_dir / "claude-plugins-official" / "superpowers" / "5.0.7" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "brainstorm.md").write_text("---\ndescription: deprecated\n---\n")

    registry = SkillRegistry(plugins_dir, state_path)
    skills = registry.scan()
    # Should still be 3, not 4
    assert len(skills) == 3


def test_scan_handles_missing_dir(tmp_path: Path, state_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "nonexistent", state_path)
    skills = registry.scan()
    assert skills == []


def test_name_collision_adds_prefix(tmp_path: Path, state_path: Path) -> None:
    """Two plugins with same skill name get prefixed."""
    # Plugin A
    a_dir = tmp_path / "plugin-a" / "pkg" / "1.0" / "skills" / "review"
    a_dir.mkdir(parents=True)
    (a_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: \"Review A\"\n---\n"
    )
    # Plugin B
    b_dir = tmp_path / "plugin-b" / "pkg" / "1.0" / "skills" / "review"
    b_dir.mkdir(parents=True)
    (b_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: \"Review B\"\n---\n"
    )

    registry = SkillRegistry(tmp_path, state_path)
    registry.scan()
    commands = [s.command for s in registry.skills]
    # Both should exist, one with prefix
    assert len(commands) == 2
    assert len(set(commands)) == 2  # no duplicates
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ccbot/test_skill_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccbot.skill_registry'`

- [ ] **Step 3: SkillRegistry 구현**

```python
# src/ccbot/skill_registry.py
"""Plugin skill scanner — discovers installed Claude Code skills for Telegram command menu.

Scans ~/.claude/plugins/cache/ at startup, parses SKILL.md frontmatter to extract
name and description, and provides sorted command lists for bot.set_my_commands().

Core responsibilities:
  - Scan plugin cache directories for SKILL.md files
  - Parse YAML frontmatter (name, description)
  - Convert skill names to Telegram-compatible commands (hyphens → underscores)
  - Resolve name collisions with plugin prefix
  - Track usage per project and favorites in skill_state.json
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .utils import atomic_write_json

logger = logging.getLogger(__name__)


@dataclass
class SkillInfo:
    """A discovered plugin skill."""

    name: str  # Original skill name (e.g. "systematic-debugging")
    command: str  # Telegram command (e.g. "systematic_debugging")
    description: str  # Short description for command menu
    plugin: str  # Parent plugin name (e.g. "superpowers")
    slash_command: str  # Command to send to Claude (e.g. "/systematic-debugging")


class SkillRegistry:
    """Discovers and manages Claude Code plugin skills."""

    def __init__(self, plugins_dir: Path, state_path: Path) -> None:
        self._plugins_dir = plugins_dir
        self._state_path = state_path
        self.skills: list[SkillInfo] = []
        self._command_to_skill: dict[str, SkillInfo] = {}
        self._state: dict = {"favorites": [], "usage": {}}
        self._load_state()

    def _load_state(self) -> None:
        """Load favorites and usage stats from disk."""
        if self._state_path.is_file():
            try:
                self._state = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load skill state, using defaults")

    def _save_state(self) -> None:
        """Persist favorites and usage stats to disk."""
        atomic_write_json(self._state_path, self._state)

    def scan(self) -> list[SkillInfo]:
        """Scan plugins cache and discover all skills.

        Looks for SKILL.md files under each plugin's skills/ directory.
        Parses YAML frontmatter for name and description.
        Returns the list of discovered skills.
        """
        self.skills = []
        self._command_to_skill = {}

        if not self._plugins_dir.is_dir():
            logger.warning("Plugins directory not found: %s", self._plugins_dir)
            return []

        # Collect raw skills first, then resolve collisions
        raw_skills: list[tuple[str, str, str, str]] = []  # (name, desc, plugin, marketplace)

        for marketplace_dir in sorted(self._plugins_dir.iterdir()):
            if not marketplace_dir.is_dir():
                continue
            for plugin_dir in sorted(marketplace_dir.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                plugin_name = plugin_dir.name
                # Find version directories (e.g. 5.0.7, 1.0.0)
                for version_dir in sorted(plugin_dir.iterdir()):
                    if not version_dir.is_dir():
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
                        name, desc = self._parse_skill_md(skill_md)
                        if name:
                            raw_skills.append((name, desc, plugin_name, marketplace_dir.name))

        # Detect name collisions and resolve
        name_count: dict[str, int] = {}
        for name, _, _, _ in raw_skills:
            cmd = self._to_command(name)
            name_count[cmd] = name_count.get(cmd, 0) + 1

        seen_commands: dict[str, int] = {}
        for name, desc, plugin, marketplace in raw_skills:
            cmd = self._to_command(name)
            if name_count[cmd] > 1:
                prefix = self._to_command(plugin)[:10]
                cmd = f"{prefix}_{cmd}"
            # Handle remaining duplicates with numeric suffix
            if cmd in seen_commands:
                seen_commands[cmd] += 1
                cmd = f"{cmd}_{seen_commands[cmd]}"
            else:
                seen_commands[cmd] = 0

            skill = SkillInfo(
                name=name,
                command=cmd,
                description=desc[:256],  # Telegram limit
                plugin=plugin,
                slash_command=f"/{name}",
            )
            self.skills.append(skill)
            self._command_to_skill[cmd] = skill

        logger.info("Scanned %d skills from %s", len(self.skills), self._plugins_dir)
        return self.skills

    @staticmethod
    def _parse_skill_md(path: Path) -> tuple[str, str]:
        """Parse YAML frontmatter from a SKILL.md file.

        Returns (name, description). Returns ("", "") if parsing fails.
        """
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
        desc = ""

        for line in frontmatter.splitlines():
            line = line.strip()
            if line.startswith("name:"):
                name = line[5:].strip().strip("\"'")
            elif line.startswith("description:"):
                desc = line[12:].strip().strip("\"'")

        # Truncate description to first sentence for readability
        if ". " in desc:
            desc = desc[: desc.index(". ") + 1]

        return (name, desc)

    @staticmethod
    def _to_command(name: str) -> str:
        """Convert a skill name to a Telegram-compatible command name.

        Rules: lowercase, hyphens→underscores, strip non-alphanumeric, max 32 chars.
        """
        cmd = name.lower().replace("-", "_")
        cmd = re.sub(r"[^a-z0-9_]", "", cmd)
        return cmd[:32]

    def is_skill(self, command: str) -> bool:
        """Check if a command name maps to a registered skill."""
        return command in self._command_to_skill

    def get_slash_command(self, command: str) -> str:
        """Get the original slash command for a Telegram command name.

        E.g. "systematic_debugging" → "/systematic-debugging"
        """
        skill = self._command_to_skill.get(command)
        return skill.slash_command if skill else f"/{command}"

    def record_usage(self, command: str, project_dir: str | None) -> None:
        """Record a skill usage for a project directory."""
        if not project_dir:
            return
        usage = self._state.setdefault("usage", {})
        project_usage = usage.setdefault(project_dir, {})
        project_usage[command] = project_usage.get(command, 0) + 1
        self._save_state()

    def toggle_favorite(self, command: str) -> bool:
        """Toggle a skill's favorite status. Returns new favorite state."""
        favorites = self._state.setdefault("favorites", [])
        if command in favorites:
            favorites.remove(command)
            self._save_state()
            return False
        else:
            favorites.append(command)
            self._save_state()
            return True

    def is_favorite(self, command: str) -> bool:
        """Check if a command is favorited."""
        return command in self._state.get("favorites", [])

    def get_sorted_skills(self, project_dir: str | None = None) -> list[SkillInfo]:
        """Return skills sorted by: favorites first, then project usage, then alpha."""
        favorites = set(self._state.get("favorites", []))
        usage: dict[str, int] = {}
        if project_dir:
            usage = self._state.get("usage", {}).get(project_dir, {})

        def sort_key(s: SkillInfo) -> tuple[int, int, str]:
            is_fav = 0 if s.command in favorites else 1
            freq = -(usage.get(s.command, 0))
            return (is_fav, freq, s.command)

        return sorted(self.skills, key=sort_key)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ccbot/test_skill_registry.py -v`
Expected: 모든 테스트 PASS

- [ ] **Step 5: 린트 및 타입체크**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/skill_registry.py tests/ccbot/test_skill_registry.py && uv run ruff format --check src/ccbot/skill_registry.py tests/ccbot/test_skill_registry.py && uv run pyright src/ccbot/skill_registry.py`
Expected: 에러 없음

- [ ] **Step 6: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/skill_registry.py tests/ccbot/test_skill_registry.py
git commit -m "feat: add SkillRegistry for plugin skill scanning and management"
```

---

### Task 2: 즐겨찾기/사용빈도 정렬 테스트

**Files:**
- Modify: `tests/ccbot/test_skill_registry.py`

- [ ] **Step 1: 정렬 및 상태 관리 테스트 추가**

`tests/ccbot/test_skill_registry.py` 끝에 추가:

```python
def test_toggle_favorite(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()

    assert not registry.is_favorite("brainstorming")
    result = registry.toggle_favorite("brainstorming")
    assert result is True
    assert registry.is_favorite("brainstorming")

    result = registry.toggle_favorite("brainstorming")
    assert result is False
    assert not registry.is_favorite("brainstorming")


def test_favorite_persists_to_disk(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()
    registry.toggle_favorite("brainstorming")

    # Create new registry instance — should load saved state
    registry2 = SkillRegistry(plugins_dir, state_path)
    assert registry2.is_favorite("brainstorming")


def test_record_usage(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()
    registry.record_usage("brainstorming", "/home/user/project-a")
    registry.record_usage("brainstorming", "/home/user/project-a")
    registry.record_usage("code_reviewer", "/home/user/project-a")

    # Verify saved state
    state = json.loads(state_path.read_text())
    assert state["usage"]["/home/user/project-a"]["brainstorming"] == 2
    assert state["usage"]["/home/user/project-a"]["code_reviewer"] == 1


def test_sorted_skills_favorites_first(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()
    registry.toggle_favorite("code_reviewer")

    sorted_skills = registry.get_sorted_skills()
    assert sorted_skills[0].command == "code_reviewer"


def test_sorted_skills_usage_order(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()

    project = "/home/user/my-project"
    registry.record_usage("systematic_debugging", project)
    registry.record_usage("systematic_debugging", project)
    registry.record_usage("systematic_debugging", project)
    registry.record_usage("brainstorming", project)

    sorted_skills = registry.get_sorted_skills(project_dir=project)
    # systematic_debugging (3 uses) should come before brainstorming (1 use)
    names = [s.command for s in sorted_skills]
    assert names.index("systematic_debugging") < names.index("brainstorming")


def test_record_usage_none_project_is_noop(plugins_dir: Path, state_path: Path) -> None:
    registry = SkillRegistry(plugins_dir, state_path)
    registry.scan()
    registry.record_usage("brainstorming", None)
    assert not state_path.exists() or "usage" not in json.loads(state_path.read_text()) or json.loads(state_path.read_text())["usage"] == {}
```

- [ ] **Step 2: 테스트 실행**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ccbot/test_skill_registry.py -v`
Expected: 모든 테스트 PASS (이미 구현됨)

- [ ] **Step 3: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add tests/ccbot/test_skill_registry.py
git commit -m "test: add favorite/usage sorting tests for SkillRegistry"
```

---

### Task 3: bot.py에 스킬 커맨드 등록 연동

**Files:**
- Modify: `src/ccbot/bot.py`

- [ ] **Step 1: SkillRegistry import 및 초기화 추가**

`bot.py` 상단 import 블록에 추가 (line 60, `from .config import config` 다음):

```python
from .skill_registry import SkillRegistry
```

모듈 레벨 변수 추가 (line 159, `CC_COMMANDS` dict 아래):

```python
# Skill registry — populated at startup, used for command menu and forwarding
_skill_registry: SkillRegistry | None = None


def _get_skill_registry() -> SkillRegistry:
    """Get the skill registry singleton. Initialized in post_init."""
    assert _skill_registry is not None, "SkillRegistry not initialized"
    return _skill_registry
```

- [ ] **Step 2: post_init에서 스킬 스캔 및 커맨드 등록**

`post_init` 함수 (line 1820) 수정. `global session_monitor, _status_poll_task` 줄을:

```python
global session_monitor, _status_poll_task, _skill_registry
```

로 변경하고, `await application.bot.set_my_commands(bot_commands)` 줄 (line 1838) 직전에 스킬 등록 로직 삽입:

```python
    # Scan plugin skills and add to command menu
    plugins_dir = Path.home() / ".claude" / "plugins" / "cache"
    skill_state_path = config.config_dir / "skill_state.json"
    _skill_registry = SkillRegistry(plugins_dir, skill_state_path)
    _skill_registry.scan()

    for skill in _skill_registry.get_sorted_skills():
        bot_commands.append(BotCommand(skill.command, f"↗ {skill.description}"))
```

- [ ] **Step 3: forward_command_handler에서 스킬 usage 기록 및 원본 커맨드 복원**

`forward_command_handler` (line 487)에서, `cc_slash = cmd_text.split("@")[0]` (line 508) 바로 아래에 추가:

```python
    # If this is a skill command, convert to original slash command and record usage
    cmd_name = cc_slash.lstrip("/").split()[0]  # e.g. "systematic_debugging"
    registry = _get_skill_registry()
    if registry.is_skill(cmd_name):
        original_slash = registry.get_slash_command(cmd_name)
        # Preserve any arguments after the command
        args = cc_slash[len(cc_slash.split()[0]):]
        cc_slash = original_slash + args
        # Record usage for this project
        session_info = session_manager.get_session_info(wid)
        project_dir = session_info.get("cwd") if session_info else None
        registry.record_usage(cmd_name, project_dir)
```

주의: `wid`가 resolve된 후에 이 코드가 실행되어야 하므로, `wid = session_manager.resolve_window_for_thread(...)` (line 509)와 window 존재 체크 (line 514-518) 이후로 위치를 조정.

정확한 삽입 위치는 line 520 (`display = session_manager.get_display_name(wid)`) 직전:

```python
    # If this is a skill command, convert to original slash command and record usage
    cmd_name = cc_slash.lstrip("/").split()[0]
    registry = _get_skill_registry()
    if registry.is_skill(cmd_name):
        original_slash = registry.get_slash_command(cmd_name)
        args = cc_slash[len(cc_slash.split()[0]):]
        cc_slash = original_slash + args
        session_info = session_manager.get_session_info(wid)
        project_dir = session_info.get("cwd") if session_info else None
        registry.record_usage(cmd_name, project_dir)
```

- [ ] **Step 4: session_manager에 get_session_info 확인**

`session.py`에 `get_session_info` 메서드가 있는지 확인. 없으면 `session_map.json`에서 cwd를 가져오는 방법을 사용. `session_manager`에서 cwd를 가져오는 기존 메서드를 찾아 사용하거나, 없으면 간단히 추가.

- [ ] **Step 5: 린트 및 타입체크**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/bot.py src/ccbot/skill_registry.py && uv run pyright src/ccbot/skill_registry.py`
Expected: 에러 없음

- [ ] **Step 6: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/bot.py
git commit -m "feat: register plugin skills in Telegram command menu at startup"
```

---

### Task 4: /favorite 명령 — 텔레그램에서 즐겨찾기 토글

**Files:**
- Modify: `src/ccbot/bot.py`
- Modify: `src/ccbot/handlers/callback_data.py`

- [ ] **Step 1: callback_data에 즐겨찾기 콜백 상수 추가**

`src/ccbot/handlers/callback_data.py`에 추가:

```python
CB_FAV_TOGGLE = "fav:"
CB_FAV_PAGE = "favp:"
```

- [ ] **Step 2: /favorite 커맨드 핸들러 작성**

`bot.py`에 `/favorite` 명령 핸들러 추가 (forward_command_handler 앞, 기존 커맨드 핸들러 섹션):

```python
async def favorite_command(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show skill list with favorite toggle buttons."""
    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    registry = _get_skill_registry()
    skills = registry.get_sorted_skills()
    if not skills:
        await safe_reply(update.message, "No skills found.")
        return

    keyboard = []
    for skill in skills:
        star = "⭐ " if registry.is_favorite(skill.command) else ""
        keyboard.append([
            InlineKeyboardButton(
                f"{star}{skill.command} — {skill.description[:40]}",
                callback_data=f"{CB_FAV_TOGGLE}{skill.command}",
            )
        ])

    await safe_reply(
        update.message,
        "Tap to toggle favorite:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
```

- [ ] **Step 3: callback_handler에 즐겨찾기 토글 처리 추가**

`bot.py`의 `callback_handler` 함수에서 기존 콜백 분기에 추가:

```python
    if data.startswith(CB_FAV_TOGGLE):
        cmd = data[len(CB_FAV_TOGGLE):]
        registry = _get_skill_registry()
        is_fav = registry.toggle_favorite(cmd)
        star = "⭐ " if is_fav else ""
        await query.answer(f"{star}{cmd} {'added to' if is_fav else 'removed from'} favorites")

        # Rebuild the keyboard with updated stars
        skills = registry.get_sorted_skills()
        keyboard = []
        for skill in skills:
            s = "⭐ " if registry.is_favorite(skill.command) else ""
            keyboard.append([
                InlineKeyboardButton(
                    f"{s}{skill.command} — {skill.description[:40]}",
                    callback_data=f"{CB_FAV_TOGGLE}{skill.command}",
                )
            ])
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        # Re-register commands with new order
        bot_commands = [
            BotCommand("start", "Show welcome message"),
            BotCommand("history", "Message history for this topic"),
            BotCommand("screenshot", "Terminal screenshot with control keys"),
            BotCommand("esc", "Send Escape to interrupt Claude"),
            BotCommand("kill", "Kill session and delete topic"),
            BotCommand("unbind", "Unbind topic from session (keeps window running)"),
            BotCommand("usage", "Show Claude Code usage remaining"),
            BotCommand("favorite", "Toggle skill favorites"),
        ]
        for cmd_name, desc in CC_COMMANDS.items():
            bot_commands.append(BotCommand(cmd_name, desc))
        for skill in registry.get_sorted_skills():
            bot_commands.append(BotCommand(skill.command, f"↗ {skill.description}"))
        await query.get_bot().set_my_commands(bot_commands)
        return
```

- [ ] **Step 4: create_bot에 CommandHandler 등록**

`create_bot()` 함수에서 `application.add_handler(CommandHandler("usage", usage_command))` 바로 뒤에 추가:

```python
    application.add_handler(CommandHandler("favorite", favorite_command))
```

- [ ] **Step 5: post_init의 bot_commands에 favorite 추가**

`post_init`의 `bot_commands` 리스트에 추가:

```python
        BotCommand("favorite", "Toggle skill favorites"),
```

- [ ] **Step 6: CB_FAV_TOGGLE import 추가**

`bot.py` 상단의 `callback_data` import에 추가:

```python
    CB_FAV_TOGGLE,
```

- [ ] **Step 7: 린트 및 타입체크**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/bot.py src/ccbot/handlers/callback_data.py && uv run pyright src/ccbot/bot.py`
Expected: 에러 없음

- [ ] **Step 8: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/bot.py src/ccbot/handlers/callback_data.py
git commit -m "feat: add /favorite command for toggling skill favorites in Telegram"
```

---

### Task 5: 커맨드 재등록 헬퍼 함수 리팩토링

**Files:**
- Modify: `src/ccbot/bot.py`

- [ ] **Step 1: 커맨드 목록 빌드 로직을 헬퍼로 추출**

Task 4에서 `post_init`과 `callback_handler` 양쪽에 커맨드 빌드 로직이 중복됨. 헬퍼 함수로 추출:

```python
def _build_bot_commands() -> list[BotCommand]:
    """Build the full list of bot commands: built-in + CC + skills."""
    commands = [
        BotCommand("start", "Show welcome message"),
        BotCommand("history", "Message history for this topic"),
        BotCommand("screenshot", "Terminal screenshot with control keys"),
        BotCommand("esc", "Send Escape to interrupt Claude"),
        BotCommand("kill", "Kill session and delete topic"),
        BotCommand("unbind", "Unbind topic from session (keeps window running)"),
        BotCommand("usage", "Show Claude Code usage remaining"),
        BotCommand("favorite", "Toggle skill favorites"),
    ]
    for cmd_name, desc in CC_COMMANDS.items():
        commands.append(BotCommand(cmd_name, desc))

    if _skill_registry:
        for skill in _skill_registry.get_sorted_skills():
            commands.append(BotCommand(skill.command, f"↗ {skill.description}"))

    return commands
```

- [ ] **Step 2: post_init과 callback_handler에서 헬퍼 사용**

`post_init`에서:
```python
    await application.bot.set_my_commands(_build_bot_commands())
```

`callback_handler`의 즐겨찾기 토글 콜백에서:
```python
        await query.get_bot().set_my_commands(_build_bot_commands())
```

- [ ] **Step 3: 린트**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ccbot/bot.py && uv run ruff format --check src/ccbot/bot.py`
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
cd /Users/pakjungeol/Documents/Claude/ccbot-src
git add src/ccbot/bot.py
git commit -m "refactor: extract _build_bot_commands helper to eliminate duplication"
```

---

### Task 6: 통합 테스트 및 수동 검증

**Files:**
- Test: manual verification

- [ ] **Step 1: 전체 테스트 실행**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run pytest tests/ -v`
Expected: 모든 테스트 PASS

- [ ] **Step 2: 린트 + 타입체크 전체**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pyright src/ccbot/`
Expected: 에러 없음

- [ ] **Step 3: ccbot 재시작하여 수동 검증**

Run: `cd /Users/pakjungeol/Documents/Claude/ccbot-src && ./scripts/restart.sh`

검증 항목:
1. 텔레그램에서 `/` 입력 시 플러그인 스킬 목록이 표시되는지
2. 스킬 탭 시 Claude에 올바른 슬래시 커맨드가 전달되는지
3. `/favorite` 명령으로 즐겨찾기 토글이 동작하는지
4. 즐겨찾기 토글 후 `/` 메뉴에서 순서가 변경되는지

- [ ] **Step 4: 최종 커밋 (필요 시)**

수동 검증 중 발견된 수정사항이 있으면 커밋.
