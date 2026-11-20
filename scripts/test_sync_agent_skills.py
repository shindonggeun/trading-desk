"""scripts/sync-agent-skills.py 회귀 테스트. 표준 라이브러리만 쓴다.

    python -B -X utf8 -m unittest discover -s scripts -p "test_*.py"
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("sync_agent_skills", Path(__file__).with_name("sync-agent-skills.py"))
sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync)


class AdaptTest(unittest.TestCase):
    def test_skill_calls_become_codex_calls(self):
        self.assertEqual(sync.adapt("`/issue-start 2` · (/pr, base) · `/dev-orchestrator`"),
                         "`$issue-start 2` · ($pr, base) · `$dev-orchestrator`")

    def test_paths_are_left_alone(self):
        text = "origin/pr · /api/issue · feature/be/1-pr · /prompt · /issues · docs/pr.md"
        self.assertEqual(sync.adapt(text), text)

    def test_role_path_points_to_codex_roles(self):
        self.assertIn(".agents/roles", sync.adapt("하위 에이전트(.claude/agents)"))

    def test_role_names_become_codex_names_but_skill_names_stay(self):
        self.assertEqual(sync.adapt("`git-ops` · `senior-implementer` · `issue-start` · `dev-orchestrator`"),
                         "`git_ops` · `senior_implementer` · `issue-start` · `dev-orchestrator`")


class ProfileTest(unittest.TestCase):
    def _write(self, data):
        path = Path(tempfile.mkdtemp()) / "agent-models.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_repo_profiles_cover_every_claude_role(self):
        profiles = sync.load_codex_profiles(sync.CODEX_MODEL_CONFIG)
        self.assertEqual(set(profiles), {p.stem for p in (sync.ROOT / ".claude/agents").glob("*.md")})

    def test_bad_effort_is_rejected(self):
        path = self._write({"models": {"fast": "a", "design": "b", "work": "c"},
                            "roles": {"explorer": {"model": "fast", "effort": "huge"}}})
        with self.assertRaises(ValueError):
            sync.load_codex_profiles(path)

    def test_unknown_tier_is_rejected(self):
        path = self._write({"models": {"fast": "a", "design": "b", "work": "c"},
                            "roles": {"explorer": {"model": "turbo", "effort": "low"}}})
        with self.assertRaises(ValueError):
            sync.load_codex_profiles(path)

    def test_catalog_rejects_unknown_model(self):
        profiles = {"work": ("gpt-x", "medium")}
        catalog = self._write({"models": [{"slug": "gpt-y", "visibility": "list",
                                           "supported_reasoning_levels": [{"effort": "medium"}]}]})
        with self.assertRaises(ValueError):
            sync.validate_catalog(catalog, profiles)


class RepoInSyncTest(unittest.TestCase):
    def test_generated_files_match_sources(self):
        with redirect_stdout(io.StringIO()) as out:
            code = sync.main([])
        self.assertEqual(code, 0, out.getvalue())

    def test_every_role_has_a_codex_agent(self):
        roles = {p.stem.replace("-", "_") for p in (sync.ROOT / ".claude/agents").glob("*.md")}
        agents = {p.stem for p in (sync.ROOT / ".codex/agents").glob("*.toml")}
        self.assertEqual(roles, agents)


if __name__ == "__main__":
    unittest.main()
