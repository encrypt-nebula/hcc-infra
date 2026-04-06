# DLQ
resource "aws_sqs_queue" "dead_letter" {
  name                      = "${var.project_name}-${var.environment}-dlq"
  message_retention_seconds = 1209600 # 14 days
}

# File Upload Notification Queue
resource "aws_sqs_queue" "file_upload" {
  name                       = "${var.project_name}-${var.environment}-file-upload"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}

# Allow S3 to send messages
resource "aws_sqs_queue_policy" "file_upload" {
  queue_url = aws_sqs_queue.file_upload.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.file_upload.arn
      Condition = {
        ArnLike = {
          "aws:SourceArn" = var.raw_docs_bucket_arn
        }
      }
    }]
  })
}

# Page Processing Queue (for OCR)
resource "aws_sqs_queue" "page_processing" {
  name                       = "${var.project_name}-${var.environment}-page-processing"
  visibility_timeout_seconds = 360
  message_retention_seconds  = 3600
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}

# LLM Processing Queue (for ICD-10 Coding)
resource "aws_sqs_queue" "llm_processing" {
  name                       = "${var.project_name}-${var.environment}-llm-processing"
  visibility_timeout_seconds = 360
  message_retention_seconds  = 3600
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}

# Results Queue (final coded results)
resource "aws_sqs_queue" "results" {
  name                       = "${var.project_name}-${var.environment}-results"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}
