"""VPC + Subnet for internal communication.

Cloud Run services use "Direct VPC Egress" to reach the CozoDB VM on its
internal IP.  This avoids the $7/mo VPC Access Connector.

Pulumi concept: resources can reference other resources' outputs.  When we
write `network=vpc.id`, Pulumi knows the subnet depends on the VPC and will
create them in the right order automatically.
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services

gcp_config = pulumi.Config("gcp")
region = gcp_config.get("region") or "us-central1"

vpc = gcp.compute.Network(
    "realcomplex-vpc",
    name="realcomplex-vpc",
    auto_create_subnetworks=False,  # We manage subnets explicitly
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["compute.googleapis.com"]],
    ),
)

subnet = gcp.compute.Subnetwork(
    "realcomplex-subnet",
    name="realcomplex-subnet",
    ip_cidr_range="10.0.0.0/24",
    region=region,
    network=vpc.id,
)

# Firewall: allow internal VPC traffic on CozoDB port (TCP 9070).
# Source range is our subnet CIDR — only resources inside the VPC can connect.
cozodb_internal_firewall = gcp.compute.Firewall(
    "allow-cozodb-internal",
    name="allow-cozodb-internal",
    network=vpc.id,
    allows=[
        gcp.compute.FirewallAllowArgs(
            protocol="tcp",
            ports=["9070"],
        ),
    ],
    source_ranges=["10.0.0.0/24"],
    target_tags=["cozodb"],
)

# Firewall: allow traffic to CozoDB HTTP API (TCP 9070) on the Dedalus query VM.
# Open to 0.0.0.0/0 since the VM has an ephemeral public IP for direct access.
cozodb_firewall = gcp.compute.Firewall(
    "allow-cozodb",
    name="allow-cozodb",
    network=vpc.id,
    allows=[
        gcp.compute.FirewallAllowArgs(
            protocol="tcp",
            ports=["9070"],
        ),
    ],
    source_ranges=["0.0.0.0/0"],
    target_tags=["cozodb"],
)

# Cloud Router + NAT — lets internal-only VMs (no public IP) reach the internet
# for package installs, Docker pulls, and GCS access.  ~$1/mo.
nat_router = gcp.compute.Router(
    "realcomplex-router",
    name="realcomplex-router",
    network=vpc.id,
    region=region,
)

gcp.compute.RouterNat(
    "realcomplex-nat",
    router=nat_router.name,
    region=region,
    nat_ip_allocate_option="AUTO_ONLY",
    source_subnetwork_ip_ranges_to_nat="ALL_SUBNETWORKS_ALL_IP_RANGES",
)

# Firewall: allow IAP SSH access (35.235.240.0/20 → port 22).
# This lets `gcloud compute ssh --tunnel-through-iap` work on internal-only VMs.
gcp.compute.Firewall(
    "allow-iap-ssh",
    name="allow-iap-ssh",
    network=vpc.id,
    allows=[
        gcp.compute.FirewallAllowArgs(
            protocol="tcp",
            ports=["22"],
        ),
    ],
    source_ranges=["35.235.240.0/20"],
)

pulumi.export("vpc_id", vpc.id)
pulumi.export("subnet_id", subnet.id)
