# terraform/environments/dev/main.tf

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      Project     = var.project_name
      ManagedBy   = "terraform"
    }
  }
}

# Storage Module (S3 Buckets)
module "storage" {
  source = "../../modules/storage"

  environment  = var.environment
  project_name = var.project_name

  # Will be connected after queue module
  file_upload_queue_arn = module.queue.file_upload_queue_arn
}

# Queue Module (SQS)
module "queue" {
  source = "../../modules/queue"

  environment         = var.environment
  project_name        = var.project_name
  raw_docs_bucket_arn = module.storage.raw_docs_bucket_arn
}

# Secrets Module
module "secrets" {
  source = "../../modules/secrets"

  environment  = var.environment
  project_name = var.project_name
}

# Compute Module (Lambda)
module "compute" {
  source = "../../modules/compute"

  environment  = var.environment
  project_name = var.project_name

  # S3 Buckets
  raw_docs_bucket_name = module.storage.raw_docs_bucket_name
  raw_docs_bucket_arn  = module.storage.raw_docs_bucket_arn
  pages_bucket_name    = module.storage.pages_bucket_name
  pages_bucket_arn     = module.storage.pages_bucket_arn

  # SQS Queues
  file_upload_queue_arn     = module.queue.file_upload_queue_arn
  file_upload_queue_url     = module.queue.file_upload_queue_url
  page_processing_queue_arn = module.queue.page_processing_queue_arn
  page_processing_queue_url = module.queue.page_processing_queue_url
  llm_processing_queue_arn  = module.queue.llm_processing_queue_arn
  llm_processing_queue_url  = module.queue.llm_processing_queue_url
  results_queue_arn         = module.queue.results_queue_arn
  results_queue_url         = module.queue.results_queue_url

  # Secrets
  claude_api_key_arn = module.secrets.claude_api_key_arn

  # Database
  db_host         = "hcc-dev-rds.ch2uy8ukk87u.us-east-1.rds.amazonaws.com"
  db_name         = var.db_name
  db_username     = var.db_username
  db_password_arn = module.secrets.db_password_arn
  internal_api_key_arn = "arn:aws:secretsmanager:us-east-1:890742591306:secret:hcc-platform-dev-internal-api-key-CmiECu"
  alert_email     = var.alert_email
}

# Outputs
output "lambda_function_url" {
  value       = module.compute.lambda_function_url
  description = "Public URL to upload files"
}

output "upload_url_api" {
  value       = module.compute.upload_url_api
  description = "Public API endpoint to request S3 upload URLs"
}

output "raw_text_extractor_url" {
  value       = module.compute.raw_text_extractor_url
  description = "Public API endpoint to extract raw text from S3"
}

output "raw_docs_bucket" {
  value       = module.storage.raw_docs_bucket_name
  description = "S3 bucket for uploads"
}

output "pages_bucket" {
  value       = module.storage.pages_bucket_name
  description = "S3 bucket for processed pages"
}

output "file_upload_queue_url" {
  value       = module.queue.file_upload_queue_url
  description = "SQS queue URL for file uploads"
}

output "llm_processing_queue_url" {
  value       = module.queue.llm_processing_queue_url
  description = "SQS queue URL for LLM processing"
}

output "results_queue_url" {
  value       = module.queue.results_queue_url
  description = "SQS queue URL for final results"
}

output "lambda_function_name" {
  value       = module.compute.lambda_function_name
  description = "Lambda function name"
}
