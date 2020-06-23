# The configuration contained in this file specifies AWS resources
# related to networking.

provider "aws" {
  version = "1.60.0"
  region = "${var.region}"
}

resource "aws_vpc" "data_refinery_vpc" {
  cidr_block = "10.0.0.0/16"
  enable_dns_support = true
  enable_dns_hostnames = true

  tags {
    Name = "data-refinery-${var.user}-${var.stage}"
  }
}

resource "aws_subnet" "data_refinery_1a" {
  availability_zone = "${var.region}a"
  cidr_block = "10.0.0.0/17"
  vpc_id = "${aws_vpc.data_refinery_vpc.id}"
  map_public_ip_on_launch = true

  tags {
    Name = "data-refinery-1a-${var.user}-${var.stage}"
  }
}

resource "aws_subnet" "data_refinery_1b" {
  availability_zone = "${var.region}b"
  cidr_block = "10.0.128.0/17"
  vpc_id = "${aws_vpc.data_refinery_vpc.id}"
  # Unsure if this should be set to true
  map_public_ip_on_launch = true

  tags {
    Name = "data-refinery-1b-${var.user}-${var.stage}"
  }
}

resource "aws_internet_gateway" "data_refinery" {
  vpc_id = "${aws_vpc.data_refinery_vpc.id}"

  tags = {
    Name = "data-refinery-${var.user}-${var.stage}"
  }
}

# Note: this is a insecure practice long term, however it's
# necessary to access it from lab machines.
resource "aws_route_table" "data_refinery" {
  vpc_id = "${aws_vpc.data_refinery_vpc.id}"

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = "${aws_internet_gateway.data_refinery.id}"
  }

  tags {
    Name = "data-refinery-${var.user}-${var.stage}"
  }
}

resource "aws_route_table_association" "data_refinery_1a" {
  subnet_id      = "${aws_subnet.data_refinery_1a.id}"
  route_table_id = "${aws_route_table.data_refinery.id}"
}

resource "aws_route_table_association" "data_refinery_1b" {
  subnet_id      = "${aws_subnet.data_refinery_1b.id}"
  route_table_id = "${aws_route_table.data_refinery.id}"
}

resource "aws_db_subnet_group" "data_refinery" {
  name = "data-refinery-${var.user}-${var.stage}"
  subnet_ids = ["${aws_subnet.data_refinery_1a.id}", "${aws_subnet.data_refinery_1b.id}"]

  tags {
    Name = "Data Refinery DB Subnet ${var.user}-${var.stage}"
  }
}

# Get the API a static IP address.
resource "aws_eip" "data_refinery_api_ip" {
  vpc = true

  tags {
    Name = "Data Refinery API Elastic IP ${var.user}-${var.stage}"
  }
}

# As per https://aws.amazon.com/elasticloadbalancing/details/:
#
# You can select the appropriate load balancer based on your
# application needs. If you need flexible application management, we
# recommend that you use an Application Load Balancer. If extreme
# performance and static IP is needed for your application, we
# recommend that you use a Network Load Balancer. If you have an
# existing application that was built within the EC2-Classic network,
# then you should use a Classic Load Balancer.
#
# it appears an Network Load Balancer would be best for us because we
# need a static IP address to point DNS to.
resource "aws_lb" "data_refinery_api_load_balancer" {
  # Extra short because there is a 32 char limit on this name
  name = "DR-api-${var.user}-${var.stage}"
  internal = false
  load_balancer_type = "network"

  # Only one subnet is allowed and the API lives in 1a.
  subnet_mapping {
    subnet_id = "${aws_subnet.data_refinery_1a.id}"
    allocation_id = "${aws_eip.data_refinery_api_ip.id}"
  }
}

resource "aws_lb_target_group" "api-http" {
  name = "dr-api-${var.user}-${var.stage}-http"
  port = 80
  protocol = "TCP"
  vpc_id = "${aws_vpc.data_refinery_vpc.id}"
  stickiness = []
}

resource "aws_lb_listener" "api-http" {
  load_balancer_arn = "${aws_lb.data_refinery_api_load_balancer.arn}"
  protocol = "TCP"
  port = 80

  default_action {
    target_group_arn = "${aws_lb_target_group.api-http.arn}"
    type = "forward"
  }
}

resource "aws_lb_target_group_attachment" "api-http" {
  target_group_arn = "${aws_lb_target_group.api-http.arn}"
  target_id = "${aws_instance.api_server_1.id}"
  port = 80
}

resource "aws_lb_target_group" "api-https" {
  name = "dr-api-${var.user}-${var.stage}-https"
  port = 443
  protocol = "TCP"
  vpc_id = "${aws_vpc.data_refinery_vpc.id}"
  stickiness = []
}

resource "aws_lb_listener" "api-https" {
  load_balancer_arn = "${aws_lb.data_refinery_api_load_balancer.arn}"
  protocol = "TCP"
  port = 443

  default_action {
    target_group_arn = "${aws_lb_target_group.api-https.arn}"
    type = "forward"
  }
}

resource "aws_lb_target_group_attachment" "api-https" {
  target_group_arn = "${aws_lb_target_group.api-https.arn}"
  target_id = "${aws_instance.api_server_1.id}"
  port = 443
}

# Kurt figured this out
locals {
  stage_with_dot = "${var.stage}."
}

# This is the SSL certificate for the site itself. We can use ACM for
# this since we're using cloudfront. The cert for the API is created
# an installed by certbot.
resource "aws_acm_certificate" "ssl-cert" {
  # We don't need this resource for dev stacks.
  # Apparently `count` is the officially recommended way to conditionally enable or disable a resource:
  # https://github.com/hashicorp/terraform/issues/1604#issuecomment-266070770
  count = "${var.stage == "dev" ? 0 : 1}"

  domain_name = "${ var.stage == "prod" ? "www." : local.stage_with_dot }refine.bio"
  validation_method = "DNS"

  tags {
    Environment = "data-refinery-circleci-${var.stage}"
  }
}

resource "aws_acm_certificate_validation" "ssl-cert" {
  # We don't need this resource for dev stacks.
  # Apparently `count` is the officially recommended way to conditionally enable or disable a resource:
  # https://github.com/hashicorp/terraform/issues/1604#issuecomment-266070770
  count = "${var.stage == "dev" ? 0 : 1}"

  certificate_arn = "${aws_acm_certificate.ssl-cert.arn}"
}

resource "aws_cloudfront_distribution" "static-distribution" {
  # We don't need this resource for dev stacks.
  # Apparently `count` is the officially recommended way to conditionally enable or disable a resource:
  # https://github.com/hashicorp/terraform/issues/1604#issuecomment-266070770
  count = "${var.stage == "dev" ? 0 : 1}"

  aliases = ["${aws_acm_certificate.ssl-cert.domain_name}"]

  origin {
    domain_name = "${aws_s3_bucket.data-refinery-static.website_endpoint}"
    origin_id = "data-refinery-${var.user}-${var.stage}"

    custom_origin_config {
      origin_protocol_policy = "http-only"
      http_port = "80"
      https_port = "443"
      origin_ssl_protocols =  ["TLSv1.2", "TLSv1.1", "TLSv1"]
    }
  }

  enabled = true
  default_root_object = "index.html"

  logging_config {
    include_cookies = false
    bucket = "${aws_s3_bucket.data-refinery-static-access-logs.bucket_domain_name}"
  }

  # This makes refreshing pages in nested paths work:
  # https://stackoverflow.com/a/35673266/6095378
  custom_error_response {
    error_caching_min_ttl = 0
    error_code = 404
    response_code = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_caching_min_ttl = 0
    error_code = 403
    response_code = 200
    response_page_path = "/index.html"
  }

  default_cache_behavior {
    allowed_methods  = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "data-refinery-${var.user}-${var.stage}"

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }

    viewer_protocol_policy = "redirect-to-https"

    # As per: https://aws.amazon.com/cloudfront/pricing/
    #   "If you are using an AWS origin, effective December 1, 2014,
    #   data transferred from origin to edge locations (Amazon
    #   CloudFront "origin fetches") will be free of charge."
    # Which means that having a TTL won't save us money, but might
    # make the site load faster for certain users. However it can also
    # cause users to get an outdated site for the duration of the
    # TTL. Accuracy trumps speed for us so TTL's aren't worth it.
    min_ttl = 0
    default_ttl = 0
    max_ttl = 0

    # https://www.terraform.io/docs/providers/aws/r/cloudfront_distribution.html#compress
    compress = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  price_class = "PriceClass_100"

  tags {
    Environment = "data-refinery-${var.user}-${var.stage}"
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    acm_certificate_arn = "${aws_acm_certificate_validation.ssl-cert.certificate_arn}"
    ssl_support_method = "sni-only"
  }
}
