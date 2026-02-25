variable "app_name" {
  default = "hcc"
}

variable "app_domain" {
  default = "medicnotes.hivemynds.com"
}

variable "region" {
  default = "us-east-1"
}

variable "stage" {
  description = "Environment stage (e.g., dev, prod)"
  default     = "dev"
}

variable "compute-stage" {
  description = "Stage for compute resources (ec2, rds). We might want to keep them same for MVP stage."
  default     = "dev"
}

variable "ami" {
  description = "AMI for EC2"
  default     = "ami-0f918f7e67a3323f0"
}

# variable "vpn_server_cert_arn" {
#   description = "ACM ARN for VPN server certificate"
# }

# variable "vpn_client_cern_arn" {
#   description = "ACM ARN for client certificate CA"
# }

# variable "db_username" {
#   description = "RDS DB Username"
# }

# variable "db_password" {
#   description = "RDS DB Password"
# }

# variable "google_outh_client_id" {
# }

# variable "google_outh_client_secret" {

# }

variable "key_name" {
  description = "EC2 key pair name"
  type        = string
}

variable "instance_type" {
  default = "t2.micro"
}
