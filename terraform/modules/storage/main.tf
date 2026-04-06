resource "aws_s3_bucket" "raw_docs" {
  bucket = "${var.project_name}-${var.environment}-raw-docs"
}

resource "aws_s3_bucket_versioning" "raw_docs" {
  bucket = aws_s3_bucket.raw_docs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_docs" {
  bucket = aws_s3_bucket.raw_docs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw_docs" {
  bucket = aws_s3_bucket.raw_docs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_docs" {
  bucket = aws_s3_bucket.raw_docs.id

  rule {
    id     = "archive-old-files"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_cors_configuration" "raw_docs" {
  bucket = aws_s3_bucket.raw_docs.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "POST", "GET"]
    allowed_origins = ["*"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket" "pages" {
  bucket = "${var.project_name}-${var.environment}-pages"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pages" {
  bucket = aws_s3_bucket.pages.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "pages" {
  bucket = aws_s3_bucket.pages.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 Event Notification → SQS
resource "aws_s3_bucket_notification" "raw_docs_notification" {
  bucket = aws_s3_bucket.raw_docs.id

  queue {
    queue_arn     = var.file_upload_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "uploads/"
  }
}
