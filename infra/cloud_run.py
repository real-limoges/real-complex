"""Cloud Run — service definitions.

Deploys Ish (Haskell, port 7333, internal-only), Garcon (Haskell, port 7444,
internal-only) and Fugue (Elixir/Phoenix, port 4000, public).  Fugue calls
the Haskell services via their respective *_URL env vars with
service-to-service auth.

Uses Cloud Run v2 API for native Direct VPC Egress support (no VPC connector).
The initial deploy uses a placeholder image; CI/CD pushes the real image via
the deploy-service.yml reusable workflow.
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services
from infra.networking import vpc, subnet
from infra.iam import fugue_runner_sa, ish_runner_sa, garcon_runner_sa
from infra.secrets import secrets

gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
region = gcp_config.get("region") or "us-central1"

PLACEHOLDER_IMAGE = "us-docker.pkg.dev/cloudrun/container/hello:latest"


def _live_image(service_name: str) -> str:
    # CI/CD owns the container image. Read whatever is currently deployed so
    # Pulumi's desired state matches live state — otherwise unrelated template
    # edits (env vars, scaling) send the full container spec and clobber the
    # real image back to the placeholder.
    try:
        svc = gcp.cloudrunv2.get_service(
            name=service_name, location=region, project=project
        )
    except Exception:
        return PLACEHOLDER_IMAGE
    if svc.templates and svc.templates[0].containers:
        return svc.templates[0].containers[0].image
    return PLACEHOLDER_IMAGE

# ---------- Secret Access for Fugue Runner SA ----------

_fugue_secret_key_base_access = gcp.secretmanager.SecretIamMember(
    "fugue-runner-secret-key-base-access",
    secret_id=secrets["fugue-secret-key-base"].id,
    role="roles/secretmanager.secretAccessor",
    member=fugue_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)


# ---------- Ish (Haskell, internal-only) ----------

ish = gcp.cloudrunv2.Service(
    "ish",
    name="ish",
    location=region,
    ingress="INGRESS_TRAFFIC_INTERNAL_ONLY",
    template=gcp.cloudrunv2.ServiceTemplateArgs(
        service_account=ish_runner_sa.email,
        scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
            min_instance_count=0,
            max_instance_count=2,
        ),
        containers=[
            gcp.cloudrunv2.ServiceTemplateContainerArgs(
                image=_live_image("ish"),
                ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                    container_port=7333,
                ),
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"memory": "512Mi", "cpu": "1"},
                ),
                envs=[
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="ISH_DB_PATH",
                        value="/data/ish.db",
                    ),
                ],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["run.googleapis.com"]],
        ignore_changes=["template.containers[0].image"],
    ),
)

# Only Fugue's SA can invoke Ish
gcp.cloudrunv2.ServiceIamMember(
    "ish-fugue-invoker",
    name=ish.name,
    location=region,
    role="roles/run.invoker",
    member=fugue_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# ---------- Garcon (Haskell, internal-only) ----------

garcon = gcp.cloudrunv2.Service(
    "garcon",
    name="garcon",
    location=region,
    ingress="INGRESS_TRAFFIC_INTERNAL_ONLY",
    template=gcp.cloudrunv2.ServiceTemplateArgs(
        service_account=garcon_runner_sa.email,
        scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
            min_instance_count=0,
            max_instance_count=2,
        ),
        containers=[
            gcp.cloudrunv2.ServiceTemplateContainerArgs(
                image=_live_image("garcon"),
                ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                    container_port=7444,
                ),
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"memory": "512Mi", "cpu": "1"},
                ),
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["run.googleapis.com"]],
        ignore_changes=["template.containers[0].image"],
    ),
)

# Only Fugue's SA can invoke Garcon
gcp.cloudrunv2.ServiceIamMember(
    "garcon-fugue-invoker",
    name=garcon.name,
    location=region,
    role="roles/run.invoker",
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
            egress="ALL_TRAFFIC",
            network_interfaces=[
                gcp.cloudrunv2.ServiceTemplateVpcAccessNetworkInterfaceArgs(
                    network=vpc.id,
                    subnetwork=subnet.id,
                ),
            ],
        ),
        containers=[
            gcp.cloudrunv2.ServiceTemplateContainerArgs(
                image=_live_image("fugue"),
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
                        name="ISH_URL",
                        value=ish.uri,
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="GARCON_URL",
                        value=garcon.uri,
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
        ],
        ignore_changes=["template.containers[0].image"],
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

pulumi.export("ish_url", ish.uri)
pulumi.export("garcon_url", garcon.uri)
pulumi.export("fugue_url", fugue.uri)
