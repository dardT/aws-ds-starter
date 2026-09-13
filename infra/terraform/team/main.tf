// Module appliqué par chaque binôme, sur son propre état.
//
// Il ne crée AUCUNE ressource partagée : il se greffe sur celles du socle, dont il lit
// les sorties. C'est ce qui permet à tous les binômes d'appliquer en même temps.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  // Configuration partielle : la clé dépend du TEAM_ID et un bloc backend n'accepte
  // aucune variable. Elle est fournie à l'init — voir la cible `make team-init`.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = var.project
      Team    = var.team_id
      Owner   = var.owner_email
    }
  }
}

data "aws_caller_identity" "current" {}

// Lecture des sorties du socle : ARN du groupe de cibles, sous-réseaux, groupes de
// sécurité, rôles. En LECTURE seule — un binôme ne peut pas écrire dans l'état du socle.
data "terraform_remote_state" "socle" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "socle/terraform.tfstate"
    region = var.region
  }
}

locals {
  socle = data.terraform_remote_state.socle.outputs

  name = "qc-${var.team_id}"

  // Streamlit doit savoir qu'il est servi sous un préfixe (décision D12). Sans cela il
  // génère ses ressources statiques sur /static/… que l'ALB ne route pas : page blanche,
  // tâche saine, logs muets.
  base_url_path = var.team_id

  log_group_name = "/ecs/${local.name}-app"
}
