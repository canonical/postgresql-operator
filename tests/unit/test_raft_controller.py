# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.raft_controller import RaftController


def _build_controller(tmp_path: Path) -> RaftController:
    controller = RaftController(MagicMock(), instance_id="rel42")
    controller.data_dir = str(tmp_path / "watcher-raft" / "rel42")
    controller.config_file = str(tmp_path / "watcher-raft" / "rel42" / "patroni-raft.yaml")
    controller.service_name = "watcher-raft-rel42"
    controller.service_file = str(tmp_path / "watcher-raft-rel42.service")
    return controller


def test_configure_detects_config_file_changes(tmp_path: Path):
    controller = _build_controller(tmp_path)

    with patch.object(controller, "_install_service", return_value=False):
        assert controller.configure("10.0.0.1:2222", ["10.0.0.2:2222"], "secret")
        assert not controller.configure("10.0.0.1:2222", ["10.0.0.2:2222"], "secret")
        assert controller.configure("10.0.0.1:2222", ["10.0.0.3:2222"], "secret")


def test_remove_service_disables_and_deletes_unit(tmp_path: Path):
    controller = _build_controller(tmp_path)
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


def test_install_service_returns_false_when_daemon_reload_fails(tmp_path: Path):
    controller = _build_controller(tmp_path)
    controller._self_addr = "10.0.0.1:2222"
    controller._partner_addrs = ["10.0.0.2:2222"]
    controller._password = "secret"

    with patch(
        "src.raft_controller.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            returncode=1,
            cmd=["/usr/bin/systemctl", "daemon-reload"],
            stderr="reload failed",
        ),
    ):
        assert not controller._install_service()
