# IAM Role for Lambda
resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-${var.environment}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

# IAM Policy for Lambda
resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:HeadObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          "arn:aws:s3:::*",
          var.raw_docs_bucket_arn,
          "${var.raw_docs_bucket_arn}/*",
          var.pages_bucket_arn,
          "${var.pages_bucket_arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [
          var.file_upload_queue_arn,
          var.page_processing_queue_arn,
          var.llm_processing_queue_arn,
          var.results_queue_arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = [
          "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0",
          "arn:aws:bedrock:us-east-1::foundation-model/us.amazon.nova-lite-v1:0"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          var.claude_api_key_arn,
          var.db_password_arn,
          var.internal_api_key_arn
        ]
      }
    ]
  })
}

# Package Lambda functions
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/workers"
  excludes    = ["pypdf.tar.gz", "__pycache__", "*.pyc"]
  output_path = "${path.module}/file_processor.zip"
}

data "archive_file" "upload_url_generator_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/workers"
  excludes    = ["pypdf.tar.gz", "__pycache__", "*.pyc"]
  output_path = "${path.module}/upload_url_generator.zip"
}

data "archive_file" "page_ocr_worker_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/workers"
  excludes    = ["pypdf.tar.gz", "__pycache__", "*.pyc"]
  output_path = "${path.module}/page_ocr_worker.zip"
}

data "archive_file" "llm_icd_coder_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/workers"
  excludes    = ["pypdf.tar.gz", "__pycache__", "*.pyc"]
  output_path = "${path.module}/llm_icd_coder.zip"
}

data "archive_file" "raw_text_extractor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/workers"
  excludes    = ["pypdf.tar.gz", "__pycache__", "*.pyc"]
  output_path = "${path.module}/raw_text_extractor.zip"
}

# Lambda Function
resource "aws_lambda_function" "file_processor" {
  filename      = data.archive_file.lambda_zip.output_path
  function_name = "${var.project_name}-${var.environment}-file-processor"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "file_processor.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 512

  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      RAW_DOCS_BUCKET      = var.raw_docs_bucket_name
      ENVIRONMENT          = var.environment
      PROJECT_NAME         = var.project_name
      INTERNAL_API_KEY_ARN = var.internal_api_key_arn
      INTERNAL_API_KEY     = "hcc-internal-secure-key-2026"
      LLM_QUEUE_URL        = var.llm_processing_queue_url
      RESULTS_QUEUE_URL    = var.results_queue_url
    }
  }
}

# Lambda Function URL (public endpoint)
resource "aws_lambda_function_url" "file_processor" {
  function_name      = aws_lambda_function.file_processor.function_name
  authorization_type = "NONE" # Public access (add auth later)

  cors {
    allow_origins = ["*"]
    allow_methods = ["POST", "GET"]
    allow_headers = ["*"]
    max_age       = 300
  }
}

# Allow public access to Lambda Function URL
resource "aws_lambda_permission" "allow_public_url" {
  statement_id           = "AllowExecutionFromURL"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.file_processor.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "allow_invoke_function_file_processor" {
  statement_id  = "AllowInvokeFunctionFileProcessor"
  action        = "lambda:InvokeFunction"
  principal     = "*"
  function_name = aws_lambda_function.file_processor.function_name
}

# Upload URL Generator Lambda
resource "aws_lambda_function" "upload_url_generator" {
  filename      = data.archive_file.upload_url_generator_zip.output_path
  function_name = "${var.project_name}-${var.environment}-upload-url-generator"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "upload_url_generator.lambda_handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 256

  source_code_hash = data.archive_file.upload_url_generator_zip.output_base64sha256

  environment {
    variables = {
      RAW_DOCS_BUCKET = var.raw_docs_bucket_name
    }
  }
}

# Lambda Function URL for Upload Generator
resource "aws_lambda_function_url" "upload_url_generator" {
  function_name      = aws_lambda_function.upload_url_generator.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["*"]
    allow_methods = ["*"]
    allow_headers = ["*"]
    max_age       = 300
  }
}

# Allow public access to Upload URL Generator
resource "aws_lambda_permission" "allow_public_upload_url" {
  statement_id           = "AllowExecutionFromURL"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.upload_url_generator.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Add the specific permission suggested by the user
resource "aws_lambda_permission" "allow_invoke_function_manual" {
  statement_id  = "AllowInvokeFunctionManual"
  action        = "lambda:InvokeFunction"
  principal     = "*"
  function_name = aws_lambda_function.upload_url_generator.function_name
}

# CloudWatch Log Group for Upload URL Generator (Removed to bypass user permission issues)
# resource "aws_cloudwatch_log_group" "upload_url_generator" {
#   name              = "/aws/lambda/${aws_lambda_function.upload_url_generator.function_name}"
#   retention_in_days = 7
# }

# CloudWatch Log Group (Removed to bypass user permission issues)
# resource "aws_cloudwatch_log_group" "file_processor" {
#   name              = "/aws/lambda/${aws_lambda_function.file_processor.function_name}"
#   retention_in_days = 7
# }

# SQS Event Source Mapping (optional - for S3 events via SQS)
resource "aws_lambda_event_source_mapping" "file_upload" {
  event_source_arn = var.file_upload_queue_arn
  function_name    = aws_lambda_function.file_processor.arn
  batch_size       = 1
}

# OCR Worker Event Mapping
resource "aws_lambda_event_source_mapping" "page_processing" {
  event_source_arn = var.page_processing_queue_arn
  function_name    = aws_lambda_function.page_ocr_worker.arn
  batch_size       = 1
}

# Aggregator Event Mapping (Results -> Aggregator)
resource "aws_lambda_event_source_mapping" "results_aggregation" {
  event_source_arn = var.results_queue_arn
  function_name    = aws_lambda_function.raw_text_extractor.arn
  batch_size       = 1
}

# LLM Coder Event Mapping
resource "aws_lambda_event_source_mapping" "llm_processing" {
  event_source_arn = var.llm_processing_queue_arn
  function_name    = aws_lambda_function.llm_icd_coder.arn
  batch_size       = 1
}

# Page OCR Worker Lambda
resource "aws_lambda_function" "page_ocr_worker" {
  filename      = data.archive_file.page_ocr_worker_zip.output_path
  function_name = "${var.project_name}-${var.environment}-page-ocr-worker"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "page_ocr_worker.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 512

  source_code_hash = data.archive_file.page_ocr_worker_zip.output_base64sha256

  environment {
    variables = {
      RAW_DOCS_BUCKET = var.raw_docs_bucket_name
      LLM_QUEUE_URL   = var.llm_processing_queue_url
    }
  }
}

# LLM ICD Coder Lambda
resource "aws_lambda_function" "llm_icd_coder" {
  filename      = data.archive_file.llm_icd_coder_zip.output_path
  function_name = "${var.project_name}-${var.environment}-llm-icd-coder"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "llm_icd_coder.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 512

  source_code_hash = data.archive_file.llm_icd_coder_zip.output_base64sha256

  environment {
    variables = {
      CLAUDE_SECRET_ID  = var.claude_api_key_arn
      RESULTS_QUEUE_URL = var.results_queue_url
      RAW_DOCS_BUCKET   = var.raw_docs_bucket_name
    }
  }
}

# Raw Text Extractor Lambda (Public API)
resource "aws_lambda_function" "raw_text_extractor" {
  filename      = data.archive_file.raw_text_extractor_zip.output_path
  function_name = "${var.project_name}-${var.environment}-raw-text-extractor"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "raw_text_extractor.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300 # Bedrock can take more time than Textract
  memory_size   = 1024 # Increased for PDF processing if needed

  source_code_hash = data.archive_file.raw_text_extractor_zip.output_base64sha256

  environment {
    variables = {
      DB_HOST             = var.db_host
      DB_NAME             = var.db_name
      DB_USER             = var.db_username
      DB_PASSWORD_ARN     = var.db_password_arn
      INTERNAL_API_KEY_ARN = var.internal_api_key_arn
      INTERNAL_API_KEY     = "hcc-internal-secure-key-2026"
      RAW_DOCS_BUCKET     = var.raw_docs_bucket_name
      PROJECT_NAME        = var.project_name
    }
  }
}

# Lambda Function URL for Raw Text Extractor
resource "aws_lambda_function_url" "raw_text_extractor" {
  function_name      = aws_lambda_function.raw_text_extractor.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["*"]
    allow_methods = ["*"]
    allow_headers = ["*"]
    max_age       = 300
  }
}

# Public permission for Raw Text Extractor URL
resource "aws_lambda_permission" "allow_public_raw_text_extractor" {
  statement_id           = "AllowExecutionFromURL"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.raw_text_extractor.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Secondary permission for manual invocation if needed
resource "aws_lambda_permission" "allow_invoke_raw_text_extractor" {
  statement_id  = "AllowInvokeFunctionManual"
  action        = "lambda:InvokeFunction"
  principal     = "*"
  function_name = aws_lambda_function.raw_text_extractor.function_name
}
