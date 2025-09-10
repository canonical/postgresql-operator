#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import json
import logging
import os
import socket
import subprocess

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import backup_operations

logger = logging.getLogger(__name__)

S3_INTEGRATOR_APP_NAME = "s3-integrator"
tls_certificates_app_name = "self-signed-certificates"
tls_channel = "latest/stable"
tls_config = {"ca-common-name": "Test CA"}

backup_id, value_before_backup, value_after_backup = "", None, None


@dataclasses.dataclass(frozen=True)
class ConnectionInformation:
    access_key_id: str
    secret_access_key: str
    bucket: str


@pytest.fixture(scope="session")
def microceph():
    if not os.environ.get("CI") == "true":
        raise Exception("Not running on CI. Skipping microceph installation")
    logger.info("Setting up TLS certificates")
    subprocess.run(["openssl", "genrsa", "-out", "./ca.key", "2048"], check=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-key",
            "./ca.key",
            "-days",
            "1024",
            "-out",
            "./ca.crt",
            "-outform",
            "PEM",
            "-subj",
            "/C=US/ST=Denial/L=Springfield/O=Dis/CN=www.example.com",
        ],
        check=True,
    )
    subprocess.run(["openssl", "genrsa", "-out", "./server.key", "2048"], check=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            "./server.key",
            "-out",
            "./server.csr",
            "-subj",
            "/C=US/ST=Denial/L=Springfield/O=Dis/CN=www.example.com",
        ],
        check=True,
    )
    host_ip = socket.gethostbyname(socket.gethostname())
    subprocess.run(
        f'echo "subjectAltName = IP:{host_ip}" > ./extfile.cnf',
        shell=True,
        check=True,
    )
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            "./server.csr",
            "-CA",
            "./ca.crt",
            "-CAkey",
            "./ca.key",
            "-CAcreateserial",
            "-out",
            "./server.crt",
            "-days",
            "365",
            "-extfile",
            "./extfile.cnf",
        ],
        check=True,
    )

    logger.info("Setting up microceph")
    subprocess.run(
        ["sudo", "snap", "install", "microceph", "--channel", "squid/stable"], check=True
    )
    subprocess.run(["sudo", "microceph", "cluster", "bootstrap"], check=True)
    subprocess.run(["sudo", "microceph", "disk", "add", "loop,1G,3"], check=True)
    subprocess.run(
        'sudo microceph enable rgw --ssl-certificate="$(sudo base64 -w0 ./server.crt)" --ssl-private-key="$(sudo base64 -w0 ./server.key)"',
        shell=True,
        check=True,
    )
    output = subprocess.run(
        [
            "sudo",
            "microceph.radosgw-admin",
            "user",
            "create",
            "--uid",
            "test",
            "--display-name",
            "test",
        ],
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    key = json.loads(output)["keys"][0]
    key_id = key["access_key"]
    secret_key = key["secret_key"]
    logger.info("Set up microceph")
    return ConnectionInformation(key_id, secret_key, _BUCKET)


_BUCKET = "testbucket"
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def cloud_credentials(microceph: ConnectionInformation) -> dict[str, str]:
    """Read cloud credentials."""
    return {
        "access-key": microceph.access_key_id,
        "secret-key": microceph.secret_access_key,
    }


@pytest.fixture(scope="session")
def cloud_configs(microceph: ConnectionInformation):
    host_ip = socket.gethostbyname(socket.gethostname())
    result = subprocess.run(
        "sudo base64 -w0 ./ca.crt", shell=True, check=True, stdout=subprocess.PIPE, text=True
    )
    base64_output = result.stdout
    return {
        "endpoint": f"https://{host_ip}",
        "bucket": microceph.bucket,
        "path": "/pg",
        "region": "",
        "s3-uri-style": "path",
        "tls-ca-chain": f"{base64_output}",
    }


async def test_backup_ceph(ops_test: OpsTest, cloud_configs, cloud_credentials, charm) -> None:
    """Build and deploy two units of PostgreSQL in microceph, test backup and restore actions."""
    await backup_operations(
        ops_test,
        S3_INTEGRATOR_APP_NAME,
        tls_certificates_app_name,
        tls_config,
        tls_channel,
        cloud_credentials,
        "ceph",
        cloud_configs,
        charm,
    )
