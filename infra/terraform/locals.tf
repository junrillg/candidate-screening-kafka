locals {
  prefix             = "${var.name}-${var.environment}"
  public_origin      = "https://${var.public_host}"
  key_prefix         = "/${var.name}/${var.environment}/keys"
  account_root       = "arn:aws:iam::${var.account_id}:root"
  services           = toset(["candidates", "screening", "web"])
  backend_services   = toset(["candidates", "screening"])
  task_names         = toset(["candidates", "screening", "web", "initializer"])
  secret_names       = toset(["candidates", "screening", "candidates-viewer", "screening-viewer", "internal"])
  interface_services = toset(["ecr.api", "ecr.dkr", "logs", "secretsmanager", "ssm", "bedrock-runtime"])
  task_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnLike = { "aws:SourceArn" = "arn:aws:ecs:${var.region}:${var.account_id}:*" } }
    }]
  })
  kms_admin = {
    Sid = "AccountDelegation", Effect = "Allow", Principal = { AWS = local.account_root }, Action = "kms:*", Resource = "*"
  }
  cluster_suffix = trimprefix(aws_msk_cluster.events.arn, "arn:aws:kafka:${var.region}:${var.account_id}:cluster/")
  topic_arns = { for name in ["candidate-submitted", "screening-completed", "candidate-changed", "screening-failed"] :
    name => "arn:aws:kafka:${var.region}:${var.account_id}:topic/${local.cluster_suffix}/${name}"
  }
  group_arns = {
    candidates = [for group in ["candidates-v1", "changes-*"] : "arn:aws:kafka:${var.region}:${var.account_id}:group/${local.cluster_suffix}/${group}"]
    screening  = ["arn:aws:kafka:${var.region}:${var.account_id}:group/${local.cluster_suffix}/screening-v1"]
  }
  write_topics = { candidates = ["candidate-submitted", "candidate-changed"], screening = ["screening-completed"] }
  read_topics  = { candidates = ["screening-completed", "candidate-changed"], screening = ["candidate-submitted"] }
}
