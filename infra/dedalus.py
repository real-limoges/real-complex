"""Dedalus — GCS bucket + CozoDB VM for Wikipedia graph data.

Dedalus is a Rust pipeline (separate repo) that extracts Wikipedia dumps into
structured graph data.  This module provisions the GCP infrastructure to store
and serve that data:

1. GCS bucket for Dedalus output (wikipedia.db, CSVs, JSON blobs)
2. GCE spot VM that pulls the DB from GCS and runs CozoDB HTTP server on boot

The VM uses spot pricing (~60-80% cheaper) with STOP on preemption so the
persistent disk survives.  Start/stop the VM as needed — no charges while stopped.

Workflow:
  Local:  dedalus pipeline → out/
  Upload: ./scripts/gcp-upload.sh -o out/
  Query:  gcloud compute instances start dedalus-query
          curl -X POST http://<ip>:9070/text-query \\
            -H "content-type: application/json" \\
            -d '{"script": "?[title] := *article{title}, :limit 10"}'
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services
from infra.networking import vpc, subnet

gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
region = gcp_config.get("region") or "us-central1"
zone = f"{region}-a"

# ---------- GCS Bucket ----------

dedalus_bucket = gcp.storage.Bucket(
    "dedalus-data",
    name=f"{project}-dedalus",
    location=region,
    storage_class="STANDARD",
    uniform_bucket_level_access=True,
    # Lifecycle: move blobs to Nearline after 30 days (they're rarely re-read)
    lifecycle_rules=[
        gcp.storage.BucketLifecycleRuleArgs(
            action=gcp.storage.BucketLifecycleRuleActionArgs(
                type="SetStorageClass",
                storage_class="NEARLINE",
            ),
            condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                age=30,
                matches_prefixes=["blobs/"],
            ),
        ),
    ],
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["compute.googleapis.com"]],
        protect=True,  # Don't accidentally delete the bucket
    ),
)

# ---------- CozoDB VM ----------

_COZO_VERSION = "0.7.6"

_STARTUP_SCRIPT = """#!/bin/bash
set -euo pipefail

LOG="/var/log/dedalus-startup.log"
exec > >(tee -a "$LOG") 2>&1

echo "$(date): Dedalus VM startup beginning..."

BUCKET="__BUCKET__"
COZO_VERSION="__COZO_VERSION__"
DATA_DIR="/mnt/disks/data/dedalus"

# Install CozoDB standalone server if not present
if ! command -v cozo &>/dev/null; then
    echo "$(date): Installing CozoDB v${COZO_VERSION}..."
    ARCH=$(dpkg --print-architecture)  # amd64 or arm64
    if [ "$ARCH" = "amd64" ]; then
        COZO_ARCH="x86_64"
    else
        COZO_ARCH="aarch64"
    fi
    COZO_URL="https://github.com/cozodb/cozo/releases/download/v${COZO_VERSION}/cozo-bin-${COZO_VERSION}-${COZO_ARCH}-unknown-linux-gnu.tar.gz"
    curl -sSfL "$COZO_URL" | tar xz -C /usr/local/bin
    chmod +x /usr/local/bin/cozo
fi

mkdir -p "${DATA_DIR}"

# Sync database from GCS (only changed files)
echo "$(date): Syncing database from GCS..."
if gcloud storage ls "gs://${BUCKET}/wikipedia.db/" &>/dev/null; then
    gcloud storage rsync "gs://${BUCKET}/wikipedia.db" "${DATA_DIR}/wikipedia.db" \
        --recursive --checksums-only
fi

# Sync CSVs
if gcloud storage ls "gs://${BUCKET}/csv/" &>/dev/null; then
    echo "$(date): Syncing CSV files..."
    mkdir -p "${DATA_DIR}/csv"
    gcloud storage rsync "gs://${BUCKET}/csv" "${DATA_DIR}/csv" \
        --recursive --checksums-only
fi

# Start CozoDB as a systemd service
cat > /etc/systemd/system/cozodb.service <<EOF
[Unit]
Description=CozoDB for Dedalus Wikipedia
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cozo server --path ${DATA_DIR}/wikipedia.db --port 9070 --bind 0.0.0.0 --engine rocksdb
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cozodb
systemctl restart cozodb

echo "$(date): Dedalus VM startup complete. CozoDB listening on :9070"
"""

# SA for the VM — needs GCS read access only
dedalus_vm_sa = gcp.serviceaccount.Account(
    "dedalus-vm",
    account_id="dedalus-vm",
    display_name="Dedalus query VM SA",
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["iam.googleapis.com"]],
    ),
)

# Grant the VM SA read access to the bucket
dedalus_bucket_reader = gcp.storage.BucketIAMMember(
    "dedalus-vm-bucket-reader",
    bucket=dedalus_bucket.name,
    role="roles/storage.objectViewer",
    member=dedalus_vm_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

dedalus_vm = gcp.compute.Instance(
    "dedalus-query",
    name="dedalus-query",
    machine_type="e2-standard-4",
    zone=zone,
    scheduling=gcp.compute.InstanceSchedulingArgs(
        provisioning_model="SPOT",
        instance_termination_action="STOP",
        preemptible=True,
        automatic_restart=False,
    ),
    boot_disk=gcp.compute.InstanceBootDiskArgs(
        initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
            image="debian-cloud/debian-12",
            size=50,
            type="pd-ssd",
        ),
    ),
    network_interfaces=[
        gcp.compute.InstanceNetworkInterfaceArgs(
            network=vpc.id,
            subnetwork=subnet.id,
            access_configs=[
                # Ephemeral external IP for SSH + data sync
                gcp.compute.InstanceNetworkInterfaceAccessConfigArgs(),
            ],
        ),
    ],
    tags=["cozodb"],
    service_account=gcp.compute.InstanceServiceAccountArgs(
        email=dedalus_vm_sa.email,
        scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
    ),
    metadata={
        "startup-script": dedalus_bucket.name.apply(
            lambda name: _STARTUP_SCRIPT.replace("__BUCKET__", name).replace(
                "__COZO_VERSION__", _COZO_VERSION
            )
        ),
    },
    opts=pulumi.ResourceOptions(
        depends_on=[dedalus_bucket_reader],
    ),
)

# ---------- Exports ----------

pulumi.export("dedalus_bucket", dedalus_bucket.name)
pulumi.export("dedalus_vm_name", dedalus_vm.name)
pulumi.export("dedalus_vm_zone", zone)
pulumi.export(
    "dedalus_vm_ip",
    dedalus_vm.network_interfaces[0].access_configs[0].nat_ip,
)
