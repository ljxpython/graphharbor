from __future__ import annotations

from pathlib import Path

import pytest


def test_build_deepagent_workspace_scopes_thread_root(tmp_path: Path) -> None:
    from langgraph_runtime_pg.deepagent_workspace import build_deepagent_workspace

    workspace = build_deepagent_workspace(
        tmp_path / "workspaces",
        tenant_id="tenant-a",
        project_id="project-a",
        thread_id="thread-a",
    )

    assert workspace.root == (tmp_path / "workspaces" / "tenant-a" / "project-a" / "thread-a").resolve()
    assert workspace.backend.cwd == workspace.root
    assert workspace.backend.virtual_mode is True


def test_resolve_skill_sources_normalizes_virtual_paths(tmp_path: Path) -> None:
    from langgraph_runtime_pg.deepagent_workspace import resolve_skill_sources

    root = tmp_path / "workspaces" / "tenant" / "project" / "thread"
    skill_dir = root / "skills" / "project" / "guardrails"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: guardrails\ndescription: Guard file access\n---\n",
        encoding="utf-8",
    )

    assert resolve_skill_sources(root, ["/skills/project/guardrails/"]) == [
        "/skills/project/guardrails/"
    ]


def test_resolve_skill_sources_rejects_symlink_escape(tmp_path: Path) -> None:
    from langgraph_runtime_pg.deepagent_workspace import resolve_skill_sources

    root = tmp_path / "workspaces" / "tenant" / "project" / "thread"
    escape_target = tmp_path / "escaped-skill"
    escape_target.mkdir()
    (escape_target / "SKILL.md").write_text(
        "---\nname: escaped\ndescription: Escapes the workspace\n---\n",
        encoding="utf-8",
    )
    skill_dir = root / "skills" / "project" / "escaped"
    skill_dir.parent.mkdir(parents=True)
    skill_dir.symlink_to(escape_target, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes workspace root"):
        resolve_skill_sources(root, ["/skills/project/escaped/"])


def test_resolve_skill_sources_requires_skill_md(tmp_path: Path) -> None:
    from langgraph_runtime_pg.deepagent_workspace import resolve_skill_sources

    root = tmp_path / "workspaces" / "tenant" / "project" / "thread"
    skill_dir = root / "skills" / "project" / "missing"
    skill_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match=r"missing SKILL\.md"):
        resolve_skill_sources(root, ["/skills/project/missing/"])


@pytest.mark.parametrize("value", ["../tenant", "tenant/project", "tenant\\project", ".", ".."])
def test_build_workspace_rejects_unsafe_scope_components(tmp_path: Path, value: str) -> None:
    from langgraph_runtime_pg.deepagent_workspace import build_deepagent_workspace

    with pytest.raises(ValueError, match="one path component"):
        build_deepagent_workspace(
            tmp_path / "workspaces",
            tenant_id=value,
            project_id="project",
            thread_id="thread",
        )


def test_build_workspace_rejects_symlinked_scope_component(tmp_path: Path) -> None:
    from langgraph_runtime_pg.deepagent_workspace import build_deepagent_workspace

    base = tmp_path / "workspaces"
    base.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (base / "tenant").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        build_deepagent_workspace(base, tenant_id="tenant", project_id="project", thread_id="thread")


@pytest.mark.parametrize("value", ["/skills/../secret", "/skills/~", "/skills\\secret"])
def test_resolve_workspace_virtual_path_rejects_forbidden_components(
    tmp_path: Path, value: str
) -> None:
    from langgraph_runtime_pg.deepagent_workspace import resolve_workspace_virtual_path

    with pytest.raises(ValueError, match="forbidden component"):
        resolve_workspace_virtual_path(tmp_path, value)
