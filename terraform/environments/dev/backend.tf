terraform {
  backend "s3" {
    bucket  = "hcc-platform-terraform-state"
    key     = "dev/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
    # DynamoDB locking disabled - we skipped this step
  }
}
