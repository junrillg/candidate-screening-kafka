# Synthetic identifiers only. Never apply this file.
account_id                = "123456789012"
availability_zones        = ["us-east-1a", "us-east-1b", "us-east-1c"]
public_host               = "screening.example.com"
public_certificate_arn    = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-4000-8000-000000000001"
ca_common_name            = "Synthetic reference service transport"
final_snapshot_identifier = "synthetic-reference-final-snapshot"
image_digests = {
  candidates = "sha256:0000000000000000000000000000000000000000000000000000000000000001"
  screening  = "sha256:0000000000000000000000000000000000000000000000000000000000000002"
  web        = "sha256:0000000000000000000000000000000000000000000000000000000000000003"
}
deployment_phase       = "register"
bootstrap_verified     = false
public_ingress_enabled = false
