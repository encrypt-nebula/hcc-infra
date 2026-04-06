# Database password
resource "aws_secretsmanager_secret" "db_password" {
  name        = "${var.project_name}-${var.environment}-db-password"
  description = "RDS database password"
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id = aws_secretsmanager_secret.db_password.id
  secret_string = jsonencode({
    password = random_password.db_password.result
  })
}

resource "random_password" "db_password" {
  length  = 32
  special = true
}

# Claude API Key (you'll add this manually later)
resource "aws_secretsmanager_secret" "claude_api_key" {
  name        = "${var.project_name}-${var.environment}-claude-api-key"
  description = "Claude API key for LLM processing"
}
