terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket  = "${var.app_name}-tf-state-bucket" # Replace with your actual bucket name
    key     = "${var.stage}/terraform.tfstate"
    region  = var.region
    encrypt = true
  }
}

provider "aws" {
  region = "us-east-1"
}