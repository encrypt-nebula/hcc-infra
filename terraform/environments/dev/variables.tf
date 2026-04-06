variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name"
  type        = string
  default     = "hcc-platform"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "alert_email" {
  description = "Email for CloudWatch alerts"
  type        = string
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "hcc_platform"
}

variable "db_username" {
  description = "Database username"
  type        = string
  default     = "admin"
}
