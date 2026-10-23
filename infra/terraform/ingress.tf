resource "aws_lb" "public" {
  name                       = "${substr(var.name, 0, 16)}-${var.environment}"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = [for subnet in aws_subnet.public : subnet.id]
  enable_deletion_protection = true
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  idle_timeout               = 120
}

resource "aws_lb_target_group" "public" {
  for_each                      = toset(["candidates", "web"])
  name_prefix                   = each.key == "candidates" ? "api-" : "web-"
  vpc_id                        = aws_vpc.main.id
  target_type                   = "ip"
  protocol                      = "HTTPS"
  port                          = 8080
  deregistration_delay          = 60
  load_balancing_algorithm_type = "round_robin"
  health_check {
    enabled             = true
    protocol            = "HTTPS"
    port                = "traffic-port"
    path                = each.key == "candidates" ? "/health/ready" : "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.public.arn
  protocol          = "HTTPS"
  port              = 443
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.public_certificate_arn
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Not found"
    }
  }
}

resource "aws_lb_listener_rule" "internal_denied" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 1
  condition {
    path_pattern { values = ["/internal", "/internal/*"] }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Not found"
    }
  }
}

resource "aws_lb_listener_rule" "maintenance" {
  count        = var.public_ingress_enabled ? 0 : 1
  listener_arn = aws_lb_listener.https.arn
  priority     = 2
  condition {
    host_header { values = [var.public_host] }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "503"
      message_body = "Service unavailable"
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  condition {
    host_header { values = [var.public_host] }
  }
  condition {
    path_pattern { values = ["/api", "/api/*"] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public["candidates"].arn
  }
}

resource "aws_lb_listener_rule" "web" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20
  condition {
    host_header { values = [var.public_host] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public["web"].arn
  }
}
