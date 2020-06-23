# EBS Volumes and S3

##
# EBS
##

resource "aws_ebs_volume" "data_refinery_ebs" {
  count = "${var.max_clients}"
  availability_zone = "${var.region}a"
  size = "${var.volume_size_in_gb}"
  type = "st1" # Throughput Optimized HDD
  tags {
    Name        = "data-refinery-ebs-${count.index}-${var.user}-${var.stage}"
    Environment = "${var.stage}"
    Index       = "${count.index}"
    User        = "${var.user}"
    Stage       = "${var.stage}"
    IsBig       = "True"
  }
}

##
# S3
##

resource "aws_s3_bucket" "data_refinery_bucket" {
  bucket = "data-refinery-s3-${var.user}-${var.stage}"
  acl    = "private"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  tags {
    Name        = "data-refinery-s3-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }
}

resource "aws_s3_bucket_public_access_block" "data_refinery_bucket" {
  bucket = "${aws_s3_bucket.data_refinery_bucket.id}"

  block_public_acls   = true
  block_public_policy = true
}

resource "aws_s3_bucket" "data_refinery_results_bucket" {
  bucket = "data-refinery-s3-results-${var.user}-${var.stage}"
  acl    = "private"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  tags {
    Name        = "data-refinery-s3-results-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }

  lifecycle_rule {
    id = "auto-delete-after-7-days-${var.user}-${var.stage}"
    prefix = ""
    enabled = true
    abort_incomplete_multipart_upload_days = 1

    expiration {
      days = 7
      expired_object_delete_marker = true
    }

    noncurrent_version_expiration {
      days = 1
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data_refinery_results_bucket" {
  bucket = "${aws_s3_bucket.data_refinery_results_bucket.id}"

  block_public_acls   = true
  block_public_policy = true
}

resource "aws_s3_bucket" "data-refinery-static" {
  bucket = "${var.static_bucket_prefix == "dev" ? var.user : var.static_bucket_prefix}${var.static_bucket_root}"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  cors_rule {
    allowed_origins = ["*"]
    allowed_methods = ["GET"]
    max_age_seconds = 3000
    allowed_headers = ["Authorization"]
  }

  tags {
    Name = "data-refinery-static-site-${var.user}-${var.stage}"
  }

  website {
    index_document = "index.html"
  }
}

resource "aws_s3_bucket" "data_refinery_transcriptome_index_bucket" {
  bucket = "data-refinery-s3-transcriptome-index-${var.user}-${var.stage}"
  acl    = "public-read"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  tags {
    Name        = "data-refinery-s3-transcriptome-index-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }
}


resource "aws_s3_bucket" "data_refinery_qn_target_bucket" {
  bucket = "data-refinery-s3-qn-target-${var.user}-${var.stage}"
  acl    = "public-read"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  tags {
    Name        = "data-refinery-s3-qn-target-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }
}


resource "aws_s3_bucket" "data_refinery_compendia_bucket" {
  bucket = "data-refinery-s3-compendia-${var.user}-${var.stage}"
  acl    = "private"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  tags {
    Name        = "data-refinery-s3-compendia-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }
}

resource "aws_s3_bucket_public_access_block" "data_refinery_compendia_bucket" {
  bucket = "${aws_s3_bucket.data_refinery_compendia_bucket.id}"

  block_public_acls   = true
  block_public_policy = true
}

resource "aws_s3_bucket" "data_refinery_cloudtrail_logs_bucket" {
  bucket = "data-refinery-s3-cloudtrail-logs-${var.user}-${var.stage}"
  acl = "private"
  force_destroy = "${var.static_bucket_prefix == "dev" ? true : false}"

  tags {
    Name = "data-refinery-s3-cloudtrail-logs-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }
}

# Passing the name attribute as `EntireBucket` enables request metrics for the bucket.
# ref: https://www.terraform.io/docs/providers/aws/r/s3_bucket_metric.html
resource "aws_s3_bucket_metric" "compendia_bucket_metrics" {
  bucket = "${aws_s3_bucket.data_refinery_compendia_bucket.bucket}"
  name   = "EntireBucket"
}

resource "aws_cloudwatch_event_rule" "compendia_object_metrics" {
  name = "data-refinery-compendia-object-metric-${var.user}-${var.stage}"
  description = "Download Compendia Events"

  event_pattern = <<PATTERN
{
  "source": [
    "aws.s3"
  ],
  "detail-type": [
    "AWS API Call via CloudTrail"
  ],
  "detail": {
    "eventSource": [
      "s3.amazonaws.com"
    ],
    "eventName": [
      "GetObject"
    ],
    "requestParameters": {
      "bucketName": [
        "${aws_s3_bucket.data_refinery_compendia_bucket.bucket}"
      ]
    }
  }
}
PATTERN
}

# arn needs to be stripped of trailing `:*`
# aws appends this when creating the event_target
resource "aws_cloudwatch_event_target" "compendia_object_metrics_target" {
  rule = "${aws_cloudwatch_event_rule.compendia_object_metrics.name}"
  target_id = "compendia-object-logs-target-${var.user}-${var.stage}"
  arn = "${substr(aws_cloudwatch_log_group.compendia_object_metrics_log_group.arn,0,length(aws_cloudwatch_log_group.compendia_object_metrics_log_group.arn) - 2)}"
}

resource "aws_s3_bucket" "data-refinery-static-access-logs" {
  bucket = "data-refinery-static-access-logs-${var.user}-${var.stage}"

  tags {
    Name = "data-refinery-static-access-logs-${var.user}-${var.stage}"
    Environment = "${var.stage}"
  }

  lifecycle_rule {
    id = "auto-delete-after-7-days-${var.user}-${var.stage}"
    prefix = ""
    enabled = true
    abort_incomplete_multipart_upload_days = 7

    expiration {
      days = 7
      expired_object_delete_marker = true
    }

    noncurrent_version_expiration {
      days = 7
    }
  }
}
