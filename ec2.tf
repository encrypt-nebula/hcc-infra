# -------------------------
# IAM ROLE FOR EC2
# -------------------------
resource "aws_iam_role" "hcc_ec2_role" {
  name = "hcc-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Attach existing policies
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.hcc_ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "secrets" {
  role       = aws_iam_role.hcc_ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/SecretsManagerReadWrite"
}

# -------------------------
# Inline Policy for Cognito
# -------------------------

resource "aws_iam_role_policy" "cognito_admin_policy" {
  name = "hcc-cognito-admin-policy"
  role = aws_iam_role.hcc_ec2_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cognito-idp:AdminCreateUser",
          "cognito-idp:AdminUpdateUserAttributes",
          "cognito-idp:AdminGetUser",
          "cognito-idp:ListUsers",
          "cognito-idp:AdminAddUserToGroup"
        ]
        Resource = "arn:aws:cognito-idp:us-east-1:890742591306:userpool/us-east-1_LN4I1DaPa"
      }
    ]
  })
}


# -------------------------
# Instance Profile
# -------------------------
resource "aws_iam_instance_profile" "hcc_instance_profile" {
  name = "hcc-instance-profile"
  role = aws_iam_role.hcc_ec2_role.name
}

# -------------------------
# SECURITY GROUP
# -------------------------
resource "aws_security_group" "hcc_service_sg" {
  name = "hcc-service-sg"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HCC Service"
    from_port   = 8080
    to_port     = 8080
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

# -------------------------
# EC2 INSTANCE
# -------------------------
resource "aws_instance" "hcc_service_ec2" {
  ami                    = "ami-053b0d53c279acc90" # Ubuntu 22.04 us-east-1
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.hcc_service_sg.id]
  iam_instance_profile   = aws_iam_instance_profile.hcc_instance_profile.name

  tags = {
    Name = "hcc-service-ec2"
  }
}

# -------------------------
# OUTPUT
# -------------------------
output "ec2_public_ip" {
  value = aws_instance.hcc_service_ec2.public_ip
}
