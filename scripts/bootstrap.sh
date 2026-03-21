#!/usr/bin/env bash
# bootstrap.sh — One-time setup for developing/deploying real-complex.
# Safe to re-run (idempotent).
set -euo pipefail

echo "=== real-complex bootstrap ==="
echo ""

# ---------- gcloud CLI ----------

if command -v gcloud &>/dev/null; then
    echo "✅ gcloud CLI found: $(gcloud --version 2>/dev/null | head -1)"
else
    echo "❌ gcloud CLI not found."
    echo "   Install it: https://cloud.google.com/sdk/docs/install"
    echo "   macOS: brew install --cask google-cloud-sdk"
    exit 1
fi

echo ""
echo "--- Authenticating with GCP ---"
echo "This will open your browser twice (user auth + application-default credentials)."
echo ""

gcloud auth login --quiet
gcloud auth application-default login --quiet
gcloud config set project realcomplex-prod

# Check billing
BILLING=$(gcloud billing projects describe realcomplex-prod --format='value(billingEnabled)' 2>/dev/null || echo "UNKNOWN")
if [ "$BILLING" = "True" ]; then
    echo "✅ Billing is enabled on realcomplex-prod"
else
    echo "⚠️  Could not confirm billing is enabled."
    echo "   Go to: https://console.cloud.google.com/billing/linkedaccount?project=realcomplex-prod"
    echo "   Billing must be enabled before pulumi up will work."
fi

# ---------- Pulumi CLI ----------

echo ""
if command -v pulumi &>/dev/null; then
    echo "✅ Pulumi CLI found: $(pulumi version)"
else
    echo "❌ Pulumi CLI not found."
    echo "   Install it: curl -fsSL https://get.pulumi.com | sh"
    exit 1
fi

echo ""
echo "--- Logging into Pulumi ---"
pulumi login

# Select or create the prod stack
pulumi stack select prod 2>/dev/null || pulumi stack init prod
echo "✅ Active stack: prod"

# ---------- Python deps ----------

echo ""
if command -v uv &>/dev/null; then
    echo "✅ uv found: $(uv --version)"
    uv sync
    echo "✅ Python dependencies installed"
else
    echo "❌ uv not found."
    echo "   Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  pulumi preview   # Dry-run to see planned changes"
echo "  pulumi up        # Deploy infrastructure"
