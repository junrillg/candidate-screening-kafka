resource "aws_cloudwatch_metric_alarm" "api_unhealthy" {
  alarm_name          = "${local.prefix}-api-unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.public.arn_suffix, TargetGroup = aws_lb_target_group.public["candidates"].arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "missing"
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${local.prefix}-api-server-errors"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.public.arn_suffix, TargetGroup = aws_lb_target_group.public["candidates"].arn_suffix }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "running_tasks" {
  for_each            = var.deployment_phase == "active" ? local.services : toset([])
  alarm_name          = "${local.prefix}-${each.key}-missing-tasks"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.app[each.key].name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = var.service_limits[each.key].min
  treat_missing_data  = "breaching"
}

resource "aws_cloudwatch_metric_alarm" "postgres_storage" {
  alarm_name          = "${local.prefix}-database-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 2147483648
  treat_missing_data  = "missing"
}

resource "aws_cloudwatch_metric_alarm" "offline_partitions" {
  alarm_name          = "${local.prefix}-offline-partitions"
  namespace           = "AWS/Kafka"
  metric_name         = "OfflinePartitionsCount"
  dimensions          = { "Cluster Name" = aws_msk_cluster.events.cluster_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "missing"
}

resource "aws_cloudwatch_metric_alarm" "consumer_lag" {
  for_each = {
    screening  = { group = "screening-v1", topic = "candidate-submitted" }
    candidates = { group = "candidates-v1", topic = "screening-completed" }
  }
  alarm_name          = "${local.prefix}-${each.key}-lag"
  namespace           = "AWS/Kafka"
  metric_name         = "MaxOffsetLag"
  dimensions          = { "Cluster Name" = aws_msk_cluster.events.cluster_name, "Consumer Group" = each.value.group, Topic = each.value.topic }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  comparison_operator = "GreaterThanThreshold"
  threshold           = 32
  treat_missing_data  = "missing"
}

# Match the approved category only; never extract payloads or arbitrary exception text.
resource "aws_cloudwatch_log_metric_filter" "critical_loop" {
  for_each       = local.backend_services
  name           = "${each.key}-critical-loop"
  log_group_name = aws_cloudwatch_log_group.app[each.key].name
  pattern        = "{ $.State.State = \"critical_loop_failed_*\" }"
  metric_transformation {
    name          = "CriticalLoopFailure"
    namespace     = local.prefix
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "critical_loop" {
  alarm_name          = "${local.prefix}-critical-loop"
  namespace           = local.prefix
  metric_name         = "CriticalLoopFailure"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
}
