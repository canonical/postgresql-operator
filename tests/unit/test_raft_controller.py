# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from jinja2 import Template
from pytest import fixture

from raft_controller import RaftController


@fixture
def controller(tmp_path: Path) -> RaftController:
    controller = RaftController(MagicMock(), instance_id="rel42")
    controller.data_dir = str(tmp_path / "watcher-raft" / "rel42")
    controller.config_file = str(tmp_path / "watcher-raft" / "rel42" / "patroni-raft.yaml")
    controller.service_name = "watcher-raft-rel42"
    controller.service_file = str(tmp_path / "watcher-raft-rel42.service")
    return controller


def test_configure(tmp_path: Path, controller: RaftController):
    with open("templates/watcher.yml.j2") as file:
        contents = file.read()
        template = Template(contents)

    expected_content = template.render(
        self_addr="10.0.0.1:2222",
        partner_addrs=["10.0.0.2:2222"],
        password="secret",
        data_dir=f"{tmp_path}/watcher-raft/rel42",
    )
    with (
        patch.object(controller, "_install_service", return_value=False),
        patch("raft_controller.render_file") as _render_file,
        patch("raft_controller.create_directory") as _create_directory,
    ):
        assert controller.configure("10.0.0.1:2222", ["10.0.0.2:2222"], "secret")

        assert _create_directory.call_count == 2
        _create_directory.assert_any_call(f"{tmp_path}/watcher-raft/rel42", 0o600)
        _create_directory.assert_any_call(f"{tmp_path}/watcher-raft/rel42/raft", 0o600)
        _render_file.assert_called_once_with(
            f"{tmp_path}/watcher-raft/rel42/patroni-raft.yaml", expected_content, 0o600
        )


def test_remove_service_disables_and_deletes_unit(tmp_path: Path, controller: RaftController):
    Path(controller.service_file).write_text("[Unit]\nDescription=test\n")

    with (
        patch.object(controller, "is_running", return_value=False),
        patch("src.raft_controller.subprocess.run") as run,
    ):
        run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="enabled", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        assert controller.remove_service()

    assert not Path(controller.service_file).exists()


def test_install_service_returns_false_when_daemon_reload_fails(
    tmp_path: Path, controller: RaftController
):
    controller._self_addr = "10.0.0.1:2222"
    controller._partner_addrs = ["10.0.0.2:2222"]
    controller._password = "secret"

    with (
        patch(
            "src.raft_controller.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd=["/usr/bin/systemctl", "daemon-reload"],
                stderr="reload failed",
            ),
        ),
        patch("raft_controller.render_file"),
        patch("raft_controller.create_directory"),
    ):
        assert not controller._install_service()


def test_install_service_uses_patroni_profile_execstart(
    tmp_path: Path, controller: RaftController
):
    controller._self_addr = "10.0.0.1:2222"
    controller._partner_addrs = ["10.0.0.2:2222"]
    controller._password = "secret"
    with open("templates/watcher.service.j2") as file:
        contents = file.read()
        template = Template(contents)

    expected_content = template.render(
        instance_id="rel42", config_file=f"{tmp_path}/watcher-raft/rel42/patroni-raft.yaml"
    )

    with (
        patch(
            "src.raft_controller.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ),
        patch("raft_controller.render_file") as _render_file,
        patch("raft_controller.create_directory"),
    ):
        assert controller._install_service()

    _render_file.assert_called_once_with(
        f"{tmp_path}/watcher-raft-rel42.service",
        expected_content,
        0o644,
        change_owner=False,
    )
