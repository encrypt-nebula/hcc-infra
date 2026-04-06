output "lambda_function_name" {
  value = aws_lambda_function.file_processor.function_name
}

output "lambda_function_arn" {
  value = aws_lambda_function.file_processor.arn
}

output "lambda_function_url" {
  value       = aws_lambda_function_url.file_processor.function_url
  description = "Public URL to invoke Lambda function"
}

output "upload_url_api" {
  value       = aws_lambda_function_url.upload_url_generator.function_url
  description = "Public API endpoint to request S3 upload URLs"
}

output "raw_text_extractor_url" {
  value       = aws_lambda_function_url.raw_text_extractor.function_url
  description = "Public API endpoint to extract raw text from S3"
}
