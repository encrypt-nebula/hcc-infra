variable "environment" {
  type = string
}

variable "project_name" {
  type = string
}

variable "raw_docs_bucket_name" {
  type = string
}

variable "raw_docs_bucket_arn" {
  type = string
}

variable "pages_bucket_name" {
  type = string
}

variable "pages_bucket_arn" {
  type = string
}

variable "file_upload_queue_arn" {
  type = string
}

variable "file_upload_queue_url" {
  type = string
}

variable "page_processing_queue_arn" {
  type = string
}

variable "page_processing_queue_url" {
  type = string
}

variable "llm_processing_queue_arn" {
  type = string
}

variable "llm_processing_queue_url" {
  type = string
}

variable "results_queue_arn" {
  type = string
}

variable "results_queue_url" {
  type = string
}

variable "claude_api_key_arn" {
  type = string
}

variable "db_host" {
  type = string
}

variable "db_name" {
  type = string
}

variable "db_username" {
  type = string
}

variable "db_password_arn" {
  type = string
}

variable "internal_api_key_arn" {
  type = string
}

variable "alert_email" {
  type = string
}
