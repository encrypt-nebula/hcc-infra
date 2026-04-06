output "file_upload_queue_arn" {
  value = aws_sqs_queue.file_upload.arn
}

output "file_upload_queue_url" {
  value = aws_sqs_queue.file_upload.url
}

output "page_processing_queue_arn" {
  value = aws_sqs_queue.page_processing.arn
}

output "page_processing_queue_url" {
  value = aws_sqs_queue.page_processing.url
}

output "llm_processing_queue_arn" {
  value = aws_sqs_queue.llm_processing.arn
}

output "llm_processing_queue_url" {
  value = aws_sqs_queue.llm_processing.url
}

output "results_queue_arn" {
  value = aws_sqs_queue.results.arn
}

output "results_queue_url" {
  value = aws_sqs_queue.results.url
}

output "dead_letter_queue_arn" {
  value = aws_sqs_queue.dead_letter.arn
}
