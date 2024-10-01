#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

import boto3
import botocore.exceptions
import pytest
from pytest_operator.plugin import OpsTest

from . import architecture
from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
)
from .juju_ import juju_major_version

logger = logging.getLogger(__name__)

s3_integrator_app_name = "s3-integrator"
if juju_major_version < 3:
    tls_certificates_app_name = "tls-certificates-operator"
    if architecture.architecture == "arm64":
        tls_channel = "legacy/edge"
    else:
        tls_channel = "legacy/stable"
    tls_config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    tls_certificates_app_name = "self-signed-certificates"
    if architecture.architecture == "arm64":
        tls_channel = "latest/edge"
    else:
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
    subprocess.run(["sudo", "openssl", "genrsa", "-out", "./ca.key", "2048"], check=True)
    subprocess.run(
        [
            "sudo",
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
    subprocess.run(["sudo", "openssl", "genrsa", "-out", "./server.key", "2048"], check=True)
    subprocess.run(
        [
            "sudo",
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
        f'echo "subjectAltName = DNS:{host_ip},IP:{host_ip}" > ./extfile.cnf',
        shell=True,
        check=True,
    )
    subprocess.run(
        [
            "sudo",
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
    subprocess.run(["sudo", "snap", "install", "microceph", "--revision", "1169"], check=True)
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
    logger.info("Creating microceph bucket")
    for attempt in range(3):
        try:
            boto3.client(
                "s3",
                endpoint_url=f"https://{host_ip}",
                aws_access_key_id=key_id,
                aws_secret_access_key=secret_key,
                verify="./ca.crt",
            ).create_bucket(Bucket=_BUCKET)
        except botocore.exceptions.EndpointConnectionError:
            if attempt == 2:
                raise
            # microceph is not ready yet
            logger.info("Unable to connect to microceph via S3. Retrying")
            time.sleep(1)
        else:
            break
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


@pytest.fixture(scope="session", autouse=True)
def clean_backups_from_buckets(cloud_configs, cloud_credentials):
    """Teardown to clean up created backups from clouds."""
    yield

    logger.info("Cleaning backups from cloud buckets")
    session = boto3.session.Session(  # pyright: ignore
        aws_access_key_id=cloud_credentials["access-key"],
        aws_secret_access_key=cloud_credentials["secret-key"],
        region_name=cloud_configs["region"],
    )
    s3 = session.resource("s3", endpoint_url=cloud_configs["endpoint"])
    bucket = s3.Bucket(cloud_configs["bucket"])

    # GCS doesn't support batch delete operation, so delete the objects one by one
    backup_path = str(Path(cloud_configs["path"]) / backup_id)
    for bucket_object in bucket.objects.filter(Prefix=backup_path):
        bucket_object.delete()


@pytest.mark.group(1)
async def test_build_and_deploy(
    ops_test: OpsTest, cloud_configs, cloud_credentials, charm
) -> None:
    """Simple test to ensure that the mysql charm gets deployed."""
    # Deploy S3 Integrator and TLS Certificates Operator.
    await ops_test.model.deploy(s3_integrator_app_name)
    await ops_test.model.deploy(tls_certificates_app_name, config=tls_config, channel=tls_channel)

    # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
    # as archive_mode is disabled after restoring the backup) and to TLS Certificates Operator
    # (to be able to create backups from replicas).
    database_app_name = f"{DATABASE_APP_NAME}-ceph"
    await ops_test.model.deploy(
        charm,
        application_name=database_app_name,
        num_units=1,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )

    await ops_test.model.relate(database_app_name, tls_certificates_app_name)
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(apps=[database_app_name], status="active", timeout=1000)

    # Configure and set access and secret keys.
    logger.info("Configuring s3-integrator.")
    await ops_test.model.applications[s3_integrator_app_name].set_config(cloud_configs)
    action = await ops_test.model.units.get(f"{s3_integrator_app_name}/0").run_action(
        "sync-s3-credentials",
        **cloud_credentials,
    )
    await action.wait()
    logger.info("Relating s3-integrator to postgresql charm.")
    await ops_test.model.relate(database_app_name, s3_integrator_app_name)

    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, s3_integrator_app_name], status="active", timeout=1500
        )
