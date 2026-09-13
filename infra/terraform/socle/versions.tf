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
  }
}
