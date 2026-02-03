# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import json
import logging
import os
import socket
import subprocess
import uuid

import boto3
import jubilant
import pytest
from pytest_operator.plugin import OpsTest

from . import architecture
from .helpers import construct_endpoint
from .jubilant_helpers import RoleAttributeValue

AWS = "AWS"
GCP = "GCP"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def charm():
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./postgresql_ubuntu@24.04-{architecture.architecture}.charm"


def get_cloud_config(cloud: str) -> tuple[dict[str, str], dict[str, str]] | None:
    # Define some configurations and credentials.
    if cloud == AWS:
        return {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "us-east-1",
        }, {
            "access-key": os.environ["AWS_ACCESS_KEY"],
            "secret-key": os.environ["AWS_SECRET_KEY"],
        }
    elif cloud == GCP:
        return {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "us-east-1",
        }, {
            "access-key": os.environ["GCP_ACCESS_KEY"],
            "secret-key": os.environ["GCP_SECRET_KEY"],
        }
    return None


def cleanup_cloud(config: dict[str, str], credentials: dict[str, str]) -> None:
    # Delete the previously created objects.
    logger.info("deleting the previously created backups")
    session = boto3.session.Session(
        aws_access_key_id=credentials["access-key"],
        aws_secret_access_key=credentials["secret-key"],
        region_name=config["region"],
    )
    s3 = session.resource(
        "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
    )
    bucket = s3.Bucket(config["bucket"])
    # GCS doesn't support batch delete operation, so delete the objects one by one.
    for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
        bucket_object.delete()


@pytest.fixture(scope="module")
async def aws_cloud_configs(ops_test: OpsTest) -> None:
    if (
        not os.environ.get("AWS_ACCESS_KEY", "").strip()
        or not os.environ.get("AWS_SECRET_KEY", "").strip()
    ):
        pytest.skip("AWS configs not set")
        return

    config, credentials = get_cloud_config(AWS)
    yield config, credentials

    cleanup_cloud(config, credentials)


@pytest.fixture(scope="module")
async def gcp_cloud_configs(ops_test: OpsTest) -> None:
    if (
        not os.environ.get("GCP_ACCESS_KEY", "").strip()
        or not os.environ.get("GCP_SECRET_KEY", "").strip()
    ):
        pytest.skip("GCP configs not set")
        return

    config, credentials = get_cloud_config(GCP)
    yield config, credentials

    cleanup_cloud(config, credentials)


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    """Pytest fixture that wraps :meth:`jubilant.with_model`.

    This adds command line parameter ``--keep-models`` (see help for details).
    """
    model = request.config.getoption("--model")
    keep_models = bool(request.config.getoption("--keep-models"))

    if model:
        juju = jubilant.Juju(model=model)
        yield juju
    else:
        with jubilant.temp_model(keep=keep_models) as juju:
            yield juju


@pytest.fixture(scope="module")
def predefined_roles() -> dict:
    """Return a list of predefined roles with their expected permissions."""
    return {
        "": {
            "auto-escalate-to-database-owner": RoleAttributeValue.REQUESTED_DATABASE,
            "permissions": {
                "connect": RoleAttributeValue.REQUESTED_DATABASE,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.REQUESTED_DATABASE,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_stats": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_read": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_dml": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.ALL_DATABASES,
            },
        },
        "charmed_backup": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.YES,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_dba": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.YES,
                "write-data": RoleAttributeValue.ALL_DATABASES,
            },
        },
        "charmed_admin": {
            "auto-escalate-to-database-owner": RoleAttributeValue.ALL_DATABASES,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.ALL_DATABASES,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.ALL_DATABASES,
            },
        },
        "CREATEDB": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.YES,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.NO,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.YES,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
    }


@pytest.fixture(scope="module")
def predefined_roles_combinations() -> list:
    """Return a list of valid combinations of predefined roles."""
    return [
        ("",),
        ("charmed_stats",),
        ("charmed_read",),
        ("charmed_dml",),
        ("charmed_admin",),
        ("charmed_admin", "CREATEDB"),
    ]


@dataclasses.dataclass(frozen=True)
class ConnectionInformation:
    access_key_id: str
    secret_access_key: str
    bucket: str
    host: str
    cert: str


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
    host_ip = socket.gethostbyname(socket.gethostname())
    result = subprocess.run(
        "base64 -w0 ./ca.crt", shell=True, check=True, stdout=subprocess.PIPE, text=True
    )
    base64_output = result.stdout
    return ConnectionInformation(key_id, secret_key, "testbucket", host_ip, base64_output)
