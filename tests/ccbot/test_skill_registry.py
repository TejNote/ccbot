"""Tests for SkillRegistry — plugin skill scanning and management."""

from pathlib import Path

from ccbot.skill_registry import SkillRegistry


def _make_skill_md(
    base: Path,
    marketplace: str,
    plugin: str,
    version: str,
    skill_name: str,
    description: str,
) -> Path:
    """Create a fake SKILL.md in the expected directory structure."""
    skill_dir = base / marketplace / plugin / version / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f'---\nname: {skill_name}\ndescription: "{description}"\n---\n\nBody text here.\n',
        encoding="utf-8",
    )
    return skill_md


class TestScan:
    def test_scan_finds_all_skills(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "brainstorming",
            "Brainstorm ideas",
        )
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "systematic-debugging",
            "Debug systematically",
        )
        _make_skill_md(
            plugins_dir,
            "official",
            "pr-review-toolkit",
            "1.0.0",
            "code-reviewer",
            "Review code",
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        skills = reg.scan()

        assert len(skills) == 3
        names = {s.name for s in skills}
        assert names == {
            "superpowers:brainstorming",
            "superpowers:systematic-debugging",
            "pr-review-toolkit:code-reviewer",
        }

    def test_scan_skips_non_skill_dirs(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        # Create a commands/ directory (should be ignored)
        commands_dir = (
            plugins_dir
            / "official"
            / "superpowers"
            / "5.0.7"
            / "commands"
            / "some-command"
        )
        commands_dir.mkdir(parents=True)
        (commands_dir / "SKILL.md").write_text(
            '---\nname: some-command\ndescription: "Should be ignored"\n---\n'
        )

        # Create a valid skill
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "brainstorming",
            "Brainstorm ideas",
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        skills = reg.scan()

        assert len(skills) == 1
        assert skills[0].name == "superpowers:brainstorming"

    def test_scan_handles_missing_dir(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "nonexistent"
        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        skills = reg.scan()

        assert skills == []


class TestCommandConversion:
    def test_command_name_converts_hyphens(self) -> None:
        assert (
            SkillRegistry._to_command("systematic-debugging") == "systematic_debugging"
        )

    def test_slash_command_preserves_original(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "systematic-debugging",
            "Debug",
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        reg.scan()

        assert (
            reg.get_slash_command("superpowers_systematic_debugging")
            == "/superpowers:systematic-debugging"
        )


class TestNameCollision:
    def test_no_collision_with_plugin_prefix(self, tmp_path: Path) -> None:
        """Different plugins with same skill dir name get unique commands via plugin prefix."""
        plugins_dir = tmp_path / "cache"
        _make_skill_md(
            plugins_dir, "official", "plugin-a", "1.0.0", "review", "Review A"
        )
        _make_skill_md(
            plugins_dir, "official", "plugin-b", "1.0.0", "review", "Review B"
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        skills = reg.scan()

        assert len(skills) == 2
        commands = {s.command for s in skills}
        assert "plugin_a_review" in commands
        assert "plugin_b_review" in commands


class TestFavorites:
    def test_toggle_favorite(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "brainstorming",
            "Brainstorm",
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        reg.scan()

        # Toggle on
        result = reg.toggle_favorite("brainstorming")
        assert result is True
        assert reg.is_favorite("brainstorming") is True

        # Toggle off
        result = reg.toggle_favorite("brainstorming")
        assert result is False
        assert reg.is_favorite("brainstorming") is False

    def test_favorite_persists_to_disk(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        state_path = tmp_path / "state.json"
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "brainstorming",
            "Brainstorm",
        )

        reg1 = SkillRegistry(plugins_dir, state_path)
        reg1.scan()
        reg1.toggle_favorite("brainstorming")

        # New instance should load persisted favorites
        reg2 = SkillRegistry(plugins_dir, state_path)
        assert reg2.is_favorite("brainstorming") is True


class TestUsage:
    def test_record_usage(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        state_path = tmp_path / "state.json"
        _make_skill_md(
            plugins_dir,
            "official",
            "superpowers",
            "5.0.7",
            "brainstorming",
            "Brainstorm",
        )

        reg = SkillRegistry(plugins_dir, state_path)
        reg.scan()
        reg.record_usage("brainstorming", "/path/to/project")
        reg.record_usage("brainstorming", "/path/to/project")

        # Verify state persisted
        import json

        state = json.loads(state_path.read_text())
        assert state["usage"]["/path/to/project"]["brainstorming"] == 2

    def test_record_usage_none_project_is_noop(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        state_path = tmp_path / "state.json"

        reg = SkillRegistry(plugins_dir, state_path)
        reg.record_usage("brainstorming", None)

        # State file should not exist (no save happened)
        assert not state_path.exists()


class TestSorting:
    def test_sorted_skills_favorites_first(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        _make_skill_md(
            plugins_dir, "official", "superpowers", "5.0.7", "aaa-skill", "First alpha"
        )
        _make_skill_md(
            plugins_dir, "official", "superpowers", "5.0.7", "zzz-skill", "Last alpha"
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        reg.scan()
        reg.toggle_favorite("superpowers_zzz_skill")

        sorted_skills = reg.get_sorted_skills()
        assert sorted_skills[0].command == "superpowers_zzz_skill"
        assert sorted_skills[1].command == "superpowers_aaa_skill"

    def test_sorted_skills_usage_order(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "cache"
        _make_skill_md(
            plugins_dir, "official", "superpowers", "5.0.7", "aaa-skill", "First alpha"
        )
        _make_skill_md(
            plugins_dir, "official", "superpowers", "5.0.7", "zzz-skill", "Last alpha"
        )

        reg = SkillRegistry(plugins_dir, tmp_path / "state.json")
        reg.scan()

        project = "/my/project"
        reg.record_usage("superpowers_zzz_skill", project)
        reg.record_usage("superpowers_zzz_skill", project)
        reg.record_usage("superpowers_aaa_skill", project)

        sorted_skills = reg.get_sorted_skills(project_dir=project)
        assert sorted_skills[0].command == "superpowers_zzz_skill"
        assert sorted_skills[1].command == "superpowers_aaa_skill"
