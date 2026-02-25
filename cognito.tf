resource "aws_cognito_user_pool" "user_pool" {
  name = "${var.app_name}-${var.stage}-user-pool"

  username_attributes = ["email"]

  auto_verified_attributes = ["email"]

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
  }

  mfa_configuration = "OFF"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  lifecycle {
    replace_triggered_by = [

    ]
  }

  tags = {
    Name = "${var.app_name}-${var.stage}-cognito"
  }
}

resource "aws_cognito_user_group" "super_admin" {
  user_pool_id = aws_cognito_user_pool.user_pool.id
  name         = "SUPER_ADMIN"
  description  = "Super Admin user"
  precedence   = 1
}

resource "aws_cognito_user_group" "admin" {
  user_pool_id = aws_cognito_user_pool.user_pool.id
  name         = "ADMIN"
  description  = "Admin users"
  precedence   = 2
}

resource "aws_cognito_user_group" "tl" {
  user_pool_id = aws_cognito_user_pool.user_pool.id
  name         = "TL"
  description  = "Team Lead users"
  precedence   = 3
}

resource "aws_cognito_user_group" "coder" {
  user_pool_id = aws_cognito_user_pool.user_pool.id
  name         = "CODER"
  description  = "Coder users"
  precedence   = 4
}


locals {
  common_callback_urls = [
    "http://localhost:3000/login",
    # "https://${aws_cloudfront_distribution.static_site.domain_name}/login"
  ]

  prod_callback_urls = [
  ]

  callback_urls = var.stage == "prod" ? concat(local.common_callback_urls, local.prod_callback_urls) : local.common_callback_urls
}


resource "aws_cognito_user_pool_client" "portal" {
  name                          = "${var.app_name}-portal-${var.stage}"
  user_pool_id                  = aws_cognito_user_pool.user_pool.id
  prevent_user_existence_errors = "ENABLED"
  supported_identity_providers  = ["COGNITO"]

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "profile", "openid"]
  allowed_oauth_flows_user_pool_client = true

  callback_urls = local.callback_urls

  logout_urls = local.callback_urls

  explicit_auth_flows = ["ALLOW_USER_AUTH", "ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.app_name}-${var.stage}" # must be globally unique
  user_pool_id = aws_cognito_user_pool.user_pool.id
}

resource "aws_cognito_resource_server" "m2m_resource_server" {
  user_pool_id = aws_cognito_user_pool.user_pool.id
  identifier   = "${var.app_name}-${var.stage}-resource-server" # Replace with your unique URI
  name         = "HCC M2M Resource Server"

  scope {
    scope_name        = "admin"
    scope_description = "Admin access to HCC API"
  }
}

resource "aws_cognito_user_pool_client" "services" {
  name         = "${var.app_name}-services-${var.stage}"
  user_pool_id = aws_cognito_user_pool.user_pool.id

  generate_secret = true

  supported_identity_providers = ["COGNITO"]

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.m2m_resource_server.identifier}/admin"]

  explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]
}

output "client_id" {
  value = aws_cognito_user_pool_client.services.id
}

output "client_secret" {
  value     = aws_cognito_user_pool_client.services.client_secret
  sensitive = true
}

# resource "aws_ssm_parameter" "client_id" {
#   name        = "/${var.app_name}/${var.stage}/client_id"
#   type        = "SecureString"
#   value       = aws_cognito_user_pool_client.services.id
#   description = "Cognito User Pool Client ID"
#   overwrite   = true
# }

# resource "aws_ssm_parameter" "client_secret" {
#   name        = "/${var.app_name}/${var.stage}/client_secret"
#   type        = "SecureString"
#   value       = aws_cognito_user_pool_client.services.client_secret
#   description = "Cognito User Pool Client Secret"
#   overwrite   = true
# }