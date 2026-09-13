provider "aws" {
  region = var.region

  // §11 impose trois tags sur TOUTE ressource. `default_tags` au niveau du provider les
  // propage automatiquement : c'est plus fiable que de les répéter sur chaque ressource,
  // où un oubli finit toujours par arriver.
  //
  // Ces tags ne sont pas décoratifs. `make destroy` s'appuie dessus pour ne supprimer que
  // ce qui appartient à la formation — sans eux, le nettoyage ne peut pas distinguer les
  // ressources des labs de celles du reste du compte.
  //
  // Team vaut "socle" ici : les ressources de ce module sont partagées par la promotion.
  // Celles propres à un groupe sont surchargées individuellement.
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
  // Même règle de dérivation que qc/config.py — les six derniers chiffres de
  // l'identifiant de compte. Si cette règle change ici, elle doit changer là-bas : les
  // deux calculent le même nom de bucket, chacun de son côté.
  account_suffix = substr(data.aws_caller_identity.current.account_id, 6, 6)

  // Nom de bucket par groupe. Unicité mondiale obligatoire pour S3.
  team_buckets = {
    for t in var.teams : t => "qc-${t}-data-${local.account_suffix}"
  }

  // Préfixes créés à vide (§11), pour que les binômes trouvent une structure prête
  // plutôt qu'un bucket nu.
  s3_prefixes = ["raw", "curated", "model", "capture", "baseline", "reports"]

  // Produit cartésien groupes × préfixes, pour un for_each à plat.
  team_prefixes = {
    for pair in setproduct(var.teams, local.s3_prefixes) :
    "${pair[0]}/${pair[1]}" => { team = pair[0], prefix = pair[1] }
  }
}
