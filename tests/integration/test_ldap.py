#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""LDAP integration test, replicated from the K8s charm's test_ldap.py.

glauth-k8s is a Kubernetes charm, so it runs on a separate Kubernetes Juju
controller and the LDAP relation is a cross-controller offer/consume relation,
following the VM LDAP how-to. The K8s controller is expected to be prepared by
concierge (named "concierge-k8s" by default, overridable via K8S_CONTROLLER_NAME),
and must expose a LoadBalancer pool (concierge's k8s bootstrap provides one) so
traefik-k8s can reach active before the ldaps-ingress integration.
"""

import base64
import hashlib
import json
import logging
import os
import tempfile
import uuid

import jubilant
import pytest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from . import architecture
from .jubilant_helpers import (
    DATABASE_APP_NAME,
    execute_query_on_unit,
    get_password,
    get_unit_address,
)

logger = logging.getLogger(__name__)

GLAUTH_PSQL_APP_NAME = "postgresql-k8s"
GLAUTH_CERT_APP_NAME = "self-signed-certificates"
GLAUTH_APP_NAME = "glauth-k8s"
GLAUTH_UTILS_APP_NAME = "glauth-utils"
K8S_CONTROLLER = os.environ.get("K8S_CONTROLLER_NAME", "concierge-k8s")
LDAP_GROUP = "superheros"
LDAP_USER = "jdoe"
LDAP_USER_PASSWORD = "ldap-sync-test"

WAIT_TIMEOUT = 1800


@pytest.mark.abort_on_fail
def test_build_and_deploy(charm) -> None:
    """Build and deploy one unit of PostgreSQL."""
    juju = jubilant.Juju()
    juju.deploy(charm, config={"profile": "testing"})
    juju.wait(
        lambda status: jubilant.all_active(status, DATABASE_APP_NAME),
        delay=10,
        timeout=WAIT_TIMEOUT,
    )


@pytest.mark.abort_on_fail
def test_glauth_integration(charm) -> None:
    """Relate the charm to a glauth-k8s stack and validate LDAP authentication."""
    if not _k8s_controller_exists():
        pytest.fail(f"requires the {K8S_CONTROLLER} Juju controller")

    glauth_psql_app_name = f"glauth-{GLAUTH_PSQL_APP_NAME}"
    k8s_model = f"glauth-{uuid.uuid4().hex[:8]}"

    juju = jubilant.Juju()
    if DATABASE_APP_NAME not in juju.status().apps:
        logger.info("Deploying the PostgreSQL charm")
        juju.deploy(charm, config={"profile": "testing"})
    juju.wait(
        lambda status: jubilant.all_active(status, DATABASE_APP_NAME),
        delay=10,
        timeout=WAIT_TIMEOUT,
    )

    # The glauth stack needs a Kubernetes cloud; add_model only takes the
    # controller, so the instance below pins the full controller:model.
    logger.info("Creating the glauth stack model on %s", K8S_CONTROLLER)
    juju_provisioner = jubilant.Juju()
    juju_provisioner.add_model(k8s_model, controller=K8S_CONTROLLER)
    juju_k8s = jubilant.Juju(model=f"{K8S_CONTROLLER}:{k8s_model}")

    try:
        # Deploy the GLAuth stack: glauth as the LDAP server, a PostgreSQL K8s
        # charm as its auth backend, certificates for ldaps, and traefik to
        # expose the ldaps ingress outside the K8s cluster.
        constraints = {"arch": architecture.architecture}
        juju_k8s.deploy(
            GLAUTH_APP_NAME,
            channel="latest/edge",
            trust=True,
            config={"ldaps_enabled": "true"},
            constraints=constraints,
        )
        juju_k8s.deploy(GLAUTH_CERT_APP_NAME, channel="1/stable", constraints=constraints)
        juju_k8s.deploy(
            GLAUTH_PSQL_APP_NAME,
            app=glauth_psql_app_name,
            channel="14/stable",
            trust=True,
            constraints=constraints,
        )
        juju_k8s.deploy("traefik-k8s", channel="stable", trust=True, constraints=constraints)
        juju_k8s.wait(
            lambda status: (
                jubilant.all_blocked(status, GLAUTH_APP_NAME)
                and jubilant.all_active(
                    status, GLAUTH_CERT_APP_NAME, glauth_psql_app_name, "traefik-k8s"
                )
            ),
            delay=10,
            timeout=WAIT_TIMEOUT,
        )

        juju_k8s.integrate(f"{GLAUTH_APP_NAME}:certificates", GLAUTH_CERT_APP_NAME)
        juju_k8s.integrate(f"{GLAUTH_APP_NAME}:pg-database", f"{glauth_psql_app_name}:database")
        juju_k8s.integrate("traefik-k8s", f"{GLAUTH_APP_NAME}:ldaps-ingress")
        juju_k8s.wait(
            lambda status: jubilant.all_active(
                status, GLAUTH_APP_NAME, glauth_psql_app_name, "traefik-k8s"
            ),
            delay=10,
            timeout=WAIT_TIMEOUT,
        )

        # Offer the LDAP endpoints and consume them from the VM model.
        logger.info("Offering the LDAP endpoints and consuming them from the VM model")
        juju_k8s.offer(GLAUTH_APP_NAME, endpoint="ldap", name="ldap")
        juju_k8s.offer(GLAUTH_APP_NAME, endpoint="send-ca-cert", name="send-ca-cert")
        juju.consume(f"{k8s_model}.ldap", controller=K8S_CONTROLLER, owner="admin")
        juju.consume(f"{k8s_model}.send-ca-cert", controller=K8S_CONTROLLER, owner="admin")

        juju.integrate("ldap", f"{DATABASE_APP_NAME}:ldap")
        juju.integrate("send-ca-cert", f"{DATABASE_APP_NAME}:receive-ca-cert")
        juju.wait(
            lambda status: jubilant.all_active(status, DATABASE_APP_NAME),
            delay=10,
            timeout=WAIT_TIMEOUT,
        )

        password = get_password()
        address = get_unit_address(juju, f"{DATABASE_APP_NAME}/0")

        # Validate the 'operator' user can still access the instance.
        execute_query_on_unit(address, password, "SELECT VERSION();")

        # --- LDAP user end-to-end flow ---
        # Map the LDAP group to a PostgreSQL group (the charm's ldap-sync sidecar
        # creates the mapped users and grants them identity_access so they match
        # the hba 'ldap' line), create the mapped role the group grants into, and
        # create the user in glauth through the glauth-utils charm.
        logger.info("Configuring the LDAP group mapping and creating the PostgreSQL group")
        juju.config(DATABASE_APP_NAME, {"ldap-map": f"{LDAP_GROUP}={LDAP_GROUP}"})
        # DDL returns no rows; append a SELECT so the helper's fetchall succeeds.
        execute_query_on_unit(address, password, f'CREATE ROLE "{LDAP_GROUP}" NOLOGIN; SELECT 1;')

        logger.info("Deploying the glauth-utils charm and creating the LDAP user")
        juju_k8s.deploy(GLAUTH_UTILS_APP_NAME, channel="edge", trust=True, constraints=constraints)
        juju_k8s.integrate(GLAUTH_UTILS_APP_NAME, GLAUTH_APP_NAME)
        juju_k8s.wait(
            lambda status: jubilant.all_active(status, GLAUTH_UTILS_APP_NAME),
            delay=10,
            timeout=WAIT_TIMEOUT,
        )

        # glauth-utils' apply-ldif action reads the file from its own container.
        password_hash = (
            "{SHA256}"
            + base64.b64encode(hashlib.sha256(LDAP_USER_PASSWORD.encode()).digest()).decode()
        )
        ldif = (
            f"dn: ou={LDAP_GROUP},dc=glauth,dc=com\n"
            "objectClass: posixGroup\n"
            f"ou: {LDAP_GROUP}\n"
            "gidNumber: 5502\n"
            f"\ndn: cn={LDAP_USER},ou={LDAP_GROUP},dc=glauth,dc=com\n"
            "changetype: add\n"
            "objectClass: posixAccount\n"
            "uidNumber: 5002\n"
            "gidNumber: 5502\n"
            f"cn: {LDAP_USER}\n"
            "sn: doe\n"
            f"uid: {LDAP_USER}\n"
            f"userPassword: {password_hash}\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".ldif", delete=False) as ldif_file:
            ldif_file.write(ldif)
        juju_k8s.scp(ldif_file.name, f"{GLAUTH_UTILS_APP_NAME}/0:/var/tmp/ldap-test.ldif")
        juju_k8s.run(
            f"{GLAUTH_UTILS_APP_NAME}/0", "apply-ldif", {"path": "/var/tmp/ldap-test.ldif"}
        ).raise_on_failure()

        # The ldap-sync sidecar runs every 30s; poll until the role materialises,
        # then authenticate AS the LDAP user through the hba 'ldap' line.
        # Diagnostic for the CI artifacts (which don't capture the snap service
        # logs): is the ldap-sync sidecar up, and did the role land?
        sync_journal = juju.exec(
            unit=f"{DATABASE_APP_NAME}/0",
            command=(
                "sudo journalctl -u snap.charmed-postgresql.ldap-sync.service -n 50 --no-pager"
            ),
        )
        logger.info(
            "ldap-sync service journal:\n%s", (sync_journal.stdout or sync_journal.stderr).strip()
        )
        roles = juju.exec(
            unit=f"{DATABASE_APP_NAME}/0",
            command=(
                'charmed-postgresql.psql -d postgres -tAc "SELECT rolname FROM pg_roles" && '
                'charmed-postgresql.psql -d postgres -tAc "SELECT r.rolname FROM '
                "pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
                "WHERE g.rolname = 'identity_access'\""
            ),
        )
        logger.info(
            "pg_roles and identity_access members: %s",
            (roles.stdout or roles.stderr).strip(),
        )
        roles = juju.exec(
            unit=f"{DATABASE_APP_NAME}/0",
            command=(
                'charmed-postgresql.psql -d postgres -tAc "SELECT rolname FROM pg_roles" && '
                'charmed-postgresql.psql -d postgres -tAc "SELECT r.rolname FROM '
                "pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
                "WHERE g.rolname = 'identity_access'\""
            ),
        )
        logger.info(
            "pg_roles and identity_access members: %s",
            (roles.stdout or roles.stderr).strip(),
        )
        logger.info("Waiting for the LDAP user to sync into PostgreSQL and authenticating")
        for attempt in Retrying(stop=stop_after_attempt(12), wait=wait_fixed(30), reraise=True):
            with attempt:
                execute_query_on_unit(address, LDAP_USER, LDAP_USER_PASSWORD, "SELECT 1;")
    finally:
        juju_cleanup = jubilant.Juju()
        juju_cleanup.destroy_model(
            f"{K8S_CONTROLLER}:{k8s_model}", destroy_storage=True, force=True
        )
        # remove-saas also drops the dependent integrations, so repeat runs on a
        # reused box converge even without the spread restore-each step. Best
        # effort: juju errors if nothing was consumed (e.g. a mid-test failure).
        try:
            juju.cli("remove-saas", "ldap", "send-ca-cert")
        except jubilant.CLIError as e:
            logger.debug("remove-saas cleanup skipped: %s", e.stderr)


def _k8s_controller_exists() -> bool:
    controllers = json.loads(
        jubilant.Juju().cli("controllers", "--format=json", include_model=False)
    )
    return K8S_CONTROLLER in controllers.get("controllers", {})
