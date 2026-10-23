resource "aws_service_discovery_http_namespace" "mesh" {
  name = local.prefix
}

resource "aws_acmpca_certificate_authority" "mesh" {
  type                            = "ROOT"
  usage_mode                      = "SHORT_LIVED_CERTIFICATE"
  permanent_deletion_time_in_days = 30
  certificate_authority_configuration {
    key_algorithm     = "EC_prime256v1"
    signing_algorithm = "SHA256WITHECDSA"
    subject { common_name = var.ca_common_name }
  }
  tags = { AmazonECSManaged = "true" }
  lifecycle { prevent_destroy = true }
}

resource "aws_acmpca_certificate" "root" {
  certificate_authority_arn   = aws_acmpca_certificate_authority.mesh.arn
  certificate_signing_request = aws_acmpca_certificate_authority.mesh.certificate_signing_request
  signing_algorithm           = "SHA256WITHECDSA"
  template_arn                = "arn:aws:acm-pca:::template/RootCACertificate/V1"
  validity {
    type  = "YEARS"
    value = 5
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_acmpca_certificate_authority_certificate" "mesh" {
  certificate_authority_arn = aws_acmpca_certificate_authority.mesh.arn
  certificate               = aws_acmpca_certificate.root.certificate
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role" "mesh" {
  name = "${local.prefix}-mesh"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "mesh" {
  role       = aws_iam_role.mesh.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRolePolicyForServiceConnectTransportLayerSecurity"
}
