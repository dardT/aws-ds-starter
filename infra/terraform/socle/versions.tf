terraform {
  // §11 impose Terraform >= 1.6. Le formateur est en 1.15, les workstations le seront
  // aussi. La borne basse reste à 1.6 pour ne pas rejeter une machine légèrement en
  // retard, mais le verrou d'état natif utilisé dans backend.tf exige 1.10 en pratique.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    // Genere le mot de passe code-server de chaque binome (ide.tf). Sans cette
    // entree, `terraform init` echoue des l'apparition de `random_password` — pas
    // `apply`, ce qui rend l'oubli visible tout de suite.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
