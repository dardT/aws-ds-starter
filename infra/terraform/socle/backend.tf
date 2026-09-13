// État distant, obligatoire (§14) : un état local serait perdu entre deux journées ou
// entre deux machines, et c'est exactement ce qu'on ne peut pas se permettre sur une
// formation de trois jours.
//
// Configuration PARTIELLE : un bloc backend n'accepte ni variable ni interpolation, et
// le nom du bucket dépend de l'identifiant de compte. Les valeurs sont donc fournies à
// l'init :
//
//   cd infra/terraform/bootstrap && terraform init && terraform apply
//   terraform output -raw backend_config > ../socle/backend.hcl
//   cd ../socle && terraform init -backend-config=backend.hcl
//
// `backend.hcl` est ignoré par git : il contient un identifiant de compte, et surtout
// il se régénère en une commande.

terraform {
  backend "s3" {}
}
