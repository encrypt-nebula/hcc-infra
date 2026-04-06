output "raw_docs_bucket_name" {
  value       = aws_s3_bucket.raw_docs.bucket
  description = "Name of the raw documents bucket"
}

output "raw_docs_bucket_arn" {
  value       = aws_s3_bucket.raw_docs.arn
  description = "ARN of the raw documents bucket"
}

output "pages_bucket_name" {
  value       = aws_s3_bucket.pages.bucket
  description = "Name of the pages bucket"
}

output "pages_bucket_arn" {
  value       = aws_s3_bucket.pages.arn
  description = "ARN of the pages bucket"
}
