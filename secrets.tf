resource "aws_secretsmanager_secret" "db_credentials" {
  name        = "${var.app_name}/${var.stage}/db"
  description = "Credentials for RDS ${var.stage} instance"
}

resource "aws_secretsmanager_secret_version" "db_credentials_version" {
  secret_id = aws_secretsmanager_secret.db_credentials.id

  secret_string = jsonencode({
    username = aws_db_instance.my_rds.username
    password = aws_db_instance.my_rds.password
    rds_url  = aws_db_instance.my_rds.address
    port     = "3306"
    db_name  = "${var.app_name}db"
  })
}