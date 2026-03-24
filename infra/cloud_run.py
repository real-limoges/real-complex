"""Cloud Run — service definitions.

Currently deploys Fugue (Elixir/Phoenix, port 4000, public).  When adding more
services (Garcon, Bloom, Funktor), copy the Fugue block and change the values —
no factory function needed.

Uses Cloud Run v2 API for native Direct VPC Egress support (no VPC connector).
The initial deploy uses a placeholder image; CI/CD pushes the real image via
the deploy-service.yml reusable workflow.
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services
from infra.networking import vpc, subnet
from infra.iam import fugue_runner_sa
from infra.secrets import secrets
from infra.cozodb import cozodb_internal_ip

gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
region = gcp_config.get("region") or "us-central1"

# ---------- Secret Access for Fugue Runner SA ----------

_fugue_secret_key_base_access = gcp.secretmanager.SecretIamMember(
    "fugue-runner-secret-key-base-access",
    secret_id=secrets["fugue-secret-key-base"].id,
    role="roles/secretmanager.secretAccessor",
    member=fugue_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

_cozodb_auth_token_access = gcp.secretmanager.SecretIamMember(
    "fugue-runner-cozodb-auth-token-access",
    secret_id=secrets["cozodb-auth-token"].id,
    role="roles/secretmanager.secretAccessor",
    member=fugue_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)


# ---------- Fugue (Elixir/Phoenix) ----------

fugue = gcp.cloudrunv2.Service(
    "fugue",
    name="fugue",
    location=region,
    ingress="INGRESS_TRAFFIC_ALL",
    template=gcp.cloudrunv2.ServiceTemplateArgs(
        service_account=fugue_runner_sa.email,
        scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
            min_instance_count=0,
            max_instance_count=2,
        ),
        vpc_access=gcp.cloudrunv2.ServiceTemplateVpcAccessArgs(
            egress="PRIVATE_RANGES_ONLY",
            network_interfaces=[
                gcp.cloudrunv2.ServiceTemplateVpcAccessNetworkInterfaceArgs(
                    network=vpc.id,
                    subnetwork=subnet.id,
                ),
            ],
        ),
        containers=[
            gcp.cloudrunv2.ServiceTemplateContainerArgs(
                # Placeholder image — CI/CD deploys the real one.
                image="us-docker.pkg.dev/cloudrun/container/hello:latest",
                ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                    container_port=4000,
                ),
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"memory": "512Mi", "cpu": "1"},
                ),
                envs=[
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="PHX_SERVER",
                        value="true",
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="SECRET_KEY_BASE",
                        value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                            secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                secret=secrets["fugue-secret-key-base"].secret_id,
                                version="latest",
                            ),
                        ),
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="COZODB_URL",
                        value=cozodb_internal_ip.apply(lambda ip: f"http://{ip}:9070"),
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="COZODB_AUTH_TOKEN",
                        value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                            secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                secret=secrets["cozodb-auth-token"].secret_id,
                                version="latest",
                            ),
                        ),
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="PHX_HOST",
                        value="realcomplex.systems",
                    ),
                ],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[
            api_services["run.googleapis.com"],
            _fugue_secret_key_base_access,
            _cozodb_auth_token_access,
        ],
    ),
)

# Allow unauthenticated access (public)
gcp.cloudrunv2.ServiceIamMember(
    "fugue-public",
    name=fugue.name,
    location=region,
    role="roles/run.invoker",
    member="allUsers",
)

# ---------- Exports ----------

pulumi.export("fugue_url", fugue.uri)
