"""容器化代码执行器集成测试（需 Docker + agentchat-code-runner 镜像）。"""
from __future__ import annotations

import shutil
import subprocess

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="Docker 不可用，跳过容器 runner 测试"
)


def _exec(monkeypatch, code: str) -> dict:
    from app.agents import code_executor
    from app.config import settings

    monkeypatch.setattr(settings, "code_exec_mode", "docker")
    monkeypatch.setattr(settings, "code_exec_image", "agentchat-code-runner:latest")
    return code_executor.execute_code(code, timeout=10.0, max_output=2000)


def test_docker_runner_returns_stdout(monkeypatch):
    r = _exec(monkeypatch, "print('docker-ok')")
    assert r.get("stdout") == "docker-ok\n", r
    assert not r.get("error"), r


def test_docker_runner_blocks_network(monkeypatch):
    r = _exec(
        monkeypatch,
        "import socket; socket.create_connection(('8.8.8.8', 53), timeout=3)",
    )
    text = (r.get("error") or "") + (r.get("stderr") or "")
    assert "unreachable" in text.lower() or "network" in text.lower(), r


def test_docker_runner_rootfs_is_readonly(monkeypatch):
    r = _exec(monkeypatch, "open('/etc/hostname', 'w').write('x')")
    text = ((r.get("error") or "") + (r.get("stderr") or "")).lower()
    assert "read-only" in text or "permission" in text or "denied" in text, r


def test_docker_runner_allows_tmpfs(monkeypatch):
    r = _exec(
        monkeypatch,
        "p='/tmp/x.txt'; open(p,'w').write('tmp-ok'); print(open(p).read())",
    )
    assert r.get("stdout") == "tmp-ok\n", r
