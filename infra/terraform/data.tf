resource "aws_kms_key" "data" {
  for_each                = toset(["storage", "secrets", "keys"])
  description             = "${local.prefix} ${each.key} encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = jsonencode({ Version = "2012-10-17", Statement = [local.kms_admin] })
  lifecycle { prevent_destroy = true }
}

resource "aws_kms_key" "logs" {
  description             = "${local.prefix} CloudWatch log encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [local.kms_admin, {
      Sid       = "RegionalLogs", Effect = "Allow", Principal = { Service = "logs.${var.region}.amazonaws.com" }
      Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"], Resource = "*"
      Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/ecs/${local.prefix}/*" } }
    }]
  })
  lifecycle { prevent_destroy = true }
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(["candidates", "screening", "web", "initializer", "mesh", "msk"])
  name              = "/ecs/${local.prefix}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
  lifecycle { prevent_destroy = true }
}

resource "aws_ecr_repository" "app" {
  for_each             = local.services
  name                 = "${local.prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data["storage"].arn
  }
  lifecycle { prevent_destroy = true }
}

# Values are securely seeded outside Terraform; no secret-version resource or data read.
resource "aws_secretsmanager_secret" "app" {
  for_each                = local.secret_names
  name                    = "${local.prefix}/${each.key}"
  kms_key_id              = aws_kms_key.data["secrets"].arn
  recovery_window_in_days = 30
  lifecycle { prevent_destroy = true }
}

resource "aws_db_subnet_group" "main" {
  name       = local.prefix
  subnet_ids = [for subnet in aws_subnet.data : subnet.id]
}

locals {
  database_parameters = {
    "rds.force_ssl"                     = "1"
    "log_statement"                     = "none"
    "log_min_error_statement"           = "panic"
    "log_error_verbosity"               = "terse"
    "log_parameter_max_length"          = "0"
    "log_parameter_max_length_on_error" = "0"
    "log_duration"                      = "0"
    "log_min_duration_statement"        = "-1"
    "log_min_duration_sample"           = "-1"
    "log_statement_sample_rate"         = "0"
    "log_transaction_sample_rate"       = "0"
  }
}

resource "aws_db_parameter_group" "main" {
  name_prefix = "${local.prefix}-"
  family      = "postgres18"
  dynamic "parameter" {
    for_each = local.database_parameters
    content {
      name  = parameter.key
      value = parameter.value
    }
  }
}

resource "aws_db_instance" "main" {
  identifier                          = local.prefix
  engine                              = "postgres"
  engine_version                      = "18.6"
  engine_lifecycle_support            = "open-source-rds-extended-support-disabled"
  instance_class                      = "db.t4g.micro"
  allocated_storage                   = 20
  max_allocated_storage               = 100
  storage_type                        = "gp3"
  storage_encrypted                   = true
  kms_key_id                          = aws_kms_key.data["storage"].arn
  username                            = "deployment_admin"
  manage_master_user_password         = true
  master_user_secret_kms_key_id       = aws_kms_key.data["secrets"].arn
  db_subnet_group_name                = aws_db_subnet_group.main.name
  parameter_group_name                = aws_db_parameter_group.main.name
  vpc_security_group_ids              = [aws_security_group.data["postgres"].id]
  publicly_accessible                 = false
  multi_az                            = true
  port                                = 5432
  backup_retention_period             = var.backup_retention_days
  backup_window                       = "03:00-03:30"
  maintenance_window                  = "sun:04:00-sun:04:30"
  delete_automated_backups            = false
  deletion_protection                 = true
  skip_final_snapshot                 = false
  final_snapshot_identifier           = var.final_snapshot_identifier
  copy_tags_to_snapshot               = true
  auto_minor_version_upgrade          = true
  apply_immediately                   = false
  iam_database_authentication_enabled = false
  lifecycle { prevent_destroy = true }
}

resource "aws_msk_configuration" "events" {
  name              = local.prefix
  kafka_versions    = ["4.1.x"]
  server_properties = <<-PROPERTIES
    auto.create.topics.enable=false
    default.replication.factor=3
    min.insync.replicas=2
    num.partitions=32
    log.retention.hours=24
    log.retention.bytes=8388608
    log.segment.bytes=8388608
    message.max.bytes=65536
    unclean.leader.election.enable=false
  PROPERTIES
}

resource "aws_msk_cluster" "events" {
  cluster_name           = local.prefix
  kafka_version          = "4.1.x"
  number_of_broker_nodes = 3
  enhanced_monitoring    = "DEFAULT"
  broker_node_group_info {
    instance_type   = "kafka.m5.large"
    client_subnets  = [for subnet in aws_subnet.data : subnet.id]
    security_groups = [aws_security_group.data["kafka"].id]
    connectivity_info {
      public_access { type = "DISABLED" }
    }
    storage_info {
      ebs_storage_info { volume_size = 100 }
    }
  }
  client_authentication {
    unauthenticated = false
    sasl {
      iam   = true
      scram = false
    }
  }
  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.data["storage"].arn
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }
  configuration_info {
    arn      = aws_msk_configuration.events.arn
    revision = aws_msk_configuration.events.latest_revision
  }
  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.app["msk"].name
      }
    }
  }
  lifecycle { prevent_destroy = true }
}
