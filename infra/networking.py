"""VPC + Subnet for internal communication.

Cloud Run services use "Direct VPC Egress" for internal VPC reachability.
This avoids the $7/mo VPC Access Connector.

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
