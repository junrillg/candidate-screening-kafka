variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Use the explicitly authorized AWS account ID."
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
  validation {
    condition     = var.region == "us-east-1"
    error_message = "This reference selects regional Nova Micro in us-east-1."
  }
}

variable "name" {
  type    = string
  default = "candidate-screening"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name)) && !strcontains(var.name, "--") && !endswith(var.name, "-")
    error_message = "Use 3–24 lower-case letters, digits or single internal hyphens."
  }
}

variable "environment" {
  type    = string
  default = "reference"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,11}$", var.environment))
    error_message = "Use 2–12 lower-case letters/digits."
  }
}

variable "availability_zones" {
  description = "Three explicit, account-available AZ names in the selected region."
  type        = list(string)
  validation {
    condition     = length(var.availability_zones) == 3 && length(distinct(var.availability_zones)) == 3 && alltrue([for az in var.availability_zones : can(regex("^us-east-1[a-z]$", az))])
    error_message = "Provide three distinct us-east-1 AZ names; no account discovery is performed."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
  validation {
    condition     = can(cidrsubnet(var.vpc_cidr, 8, 8)) && can(regex("^10\\.[0-9]+\\.0\\.0/16$", var.vpc_cidr))
    error_message = "Use a non-overlapping private 10.x.0.0/16 network."
  }
}

variable "public_host" {
  description = "Owned DNS hostname covered by public_certificate_arn; no protocol/path."
  type        = string
  validation {
    condition     = length(var.public_host) <= 253 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.public_host))
    error_message = "Use one lower-case DNS hostname."
  }
}

variable "public_certificate_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:acm:us-east-1:[0-9]{12}:certificate/[a-f0-9-]{36}$", var.public_certificate_arn)) && try(split(":", var.public_certificate_arn)[4] == var.account_id, false)
    error_message = "Provide the prevalidated same-region/account ACM certificate ARN."
  }
}

variable "image_digests" {
  description = "Reviewed ECR image sha256 digests; publishing is external, never a Terraform provisioner."
  type        = map(string)
  validation {
    condition     = toset(keys(var.image_digests)) == toset(["candidates", "screening", "web"]) && alltrue([for digest in values(var.image_digests) : can(regex("^sha256:[a-f0-9]{64}$", digest))])
    error_message = "Provide exactly candidates, screening and web SHA-256 image digests."
  }
}

variable "deployment_phase" {
  type    = string
  default = "register"
  validation {
    condition     = contains(["register", "active"], var.deployment_phase)
    error_message = "Use register (no service tasks), then active after the runbook gates."
  }
}

variable "bootstrap_verified" {
  description = "Operator attestation of actual initializer exit zero. Namespace observation/redeployment remains a separate runbook gate."
  type        = bool
  default     = false
}

variable "public_ingress_enabled" {
  description = "Open HTTPS only after service discovery redeployment and private smoke checks."
  type        = bool
  default     = false
}

variable "service_limits" {
  type = map(object({
    min = number
    max = number
  }))
  default = {
    candidates = { min = 1, max = 2 }
    screening  = { min = 1, max = 2 }
    web        = { min = 1, max = 2 }
  }
  validation {
    condition = toset(keys(var.service_limits)) == toset(["candidates", "screening", "web"]) && alltrue([
      for service, limit in var.service_limits : limit.min >= 1 && limit.max >= limit.min && limit.max <= (service == "web" ? 20 : 2) && floor(limit.min) == limit.min && floor(limit.max) == limit.max
    ])
    error_message = "Provide integer 1 <= min <= max. This deployment supports backend max 2 and web max 20; larger backend limits require a reviewed role/pool configuration change."
  }
}

variable "backup_retention_days" {
  type    = number
  default = 7
  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 35 && floor(var.backup_retention_days) == var.backup_retention_days
    error_message = "RDS encrypted backup retention must be 1–35 whole days."
  }
}

variable "log_retention_days" {
  type    = number
  default = 14
  validation {
    condition     = contains([7, 14, 30, 60, 90], var.log_retention_days)
    error_message = "Select an explicit supported log retention: 7, 14, 30, 60 or 90 days."
  }
}

variable "final_snapshot_identifier" {
  description = "Unique final-snapshot name reserved for an explicitly approved eventual retirement."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,62}$", var.final_snapshot_identifier)) && !strcontains(var.final_snapshot_identifier, "--") && !endswith(var.final_snapshot_identifier, "-")
    error_message = "Provide a unique valid final snapshot identifier."
  }
}

variable "ca_common_name" {
  description = "Approved deployment-specific private CA subject; not a public DNS certificate."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9 .-]{2,63}$", var.ca_common_name))
    error_message = "Use a 3–64 character approved private CA common name."
  }
}
