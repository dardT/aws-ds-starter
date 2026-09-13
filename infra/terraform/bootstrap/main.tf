// Amorçage de l'état Terraform.
//
// Problème d'œuf et de poule : le socle veut stocker son état dans un bucket S3, mais
// ce bucket doit exister avant. Ce module minuscule le crée, avec un état LOCAL — le
// seul endroit du projet où c'est acceptable, puisqu'il ne gère qu'une ressource et
// n'est appliqué qu'une fois.
//
//   cd infra/terraform/bootstrap && terraform init && terraform apply
//
// Ensuite seulement le socle peut s'initialiser sur son backend S3.
//
// Verrouillage : on utilise le verrou natif S3 (`use_lockfile`), disponible depuis
// Terraform 1.10. Plus besoin d'une table DynamoDB dédiée — une ressource de moins à
// créer, à payer et à expliquer.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  // Tags obligatoires (§11), propagés automatiquement à toute ressource du module.
  default_tags {
    tags = {
      Project = var.project
      Team    = "socle"
      Owner   = var.owner_email
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  // Les noms de bucket sont uniques à l'échelle mondiale : d'où le suffixe de compte.
  // Même règle de dérivation que qc/config.py — les six derniers chiffres.
  account_suffix = substr(data.aws_caller_identity.current.account_id, 6, 6)
  state_bucket   = "qc-promo-tfstate-${local.account_suffix}"
}

resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket

  // Un état Terraform détruit par erreur, c'est l'infrastructure entière qui devient
  // ingérable. On empêche donc la destruction accidentelle du bucket qui le contient.
  lifecycle {
    prevent_destroy = true
  }
}

// Le versioning n'est pas un confort ici : c'est ce qui permet de revenir à un état
// antérieur si un apply se passe mal.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  description = "Bucket d'état à passer au socle via -backend-config."
  value       = aws_s3_bucket.tfstate.id
}

output "backend_config" {
  description = "Contenu à écrire dans infra/terraform/socle/backend.hcl."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.tfstate.id}"
    key          = "socle/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
