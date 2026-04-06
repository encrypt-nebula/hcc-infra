
resource "aws_security_group" "rds_public_sg" {
  name        = "hcc-sg"
  description = "Allow public access to RDS"

  ingress {
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_db_subnet_group" "rds_subnet_group" {
  name       = "hcc-subnet-group"
  subnet_ids = data.aws_subnets.default.ids

  tags = {
    Name = "default-hcc-subnet-group"
  }
}

resource "aws_db_instance" "my_rds" {
  identifier             = "hcc-dev-rds"
  engine                 = "mysql"
  engine_version         = "8.0.45"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  username               = "hccAdmin"
  password               = "hccAdmin123"
  db_name                = "hccdb"
  publicly_accessible    = true
  vpc_security_group_ids = [aws_security_group.rds_public_sg.id]
  db_subnet_group_name   = aws_db_subnet_group.rds_subnet_group.name
  skip_final_snapshot    = true
  multi_az               = false

  tags = {
    Name = "HCCDevRds"
  }
}

output "rds_endpoint" {
  value = aws_db_instance.my_rds.endpoint
}

output "rds_details" {
  value = aws_db_instance.my_rds.db_name
}
