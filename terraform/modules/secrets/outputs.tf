output "db_password_arn" {
  value = aws_secretsmanager_secret.db_password.arn
}

output "claude_api_key_arn" {
  value = aws_secretsmanager_secret.claude_api_key.arn
}
