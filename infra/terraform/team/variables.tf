variable "team_id" {
  description = "Identifiant du binôme. Format imposé : g01, g02, ... (regex ^g[0-9]{2}$)."
  type        = string

  validation {
    condition     = can(regex("^g[0-9]{2}$", var.team_id))
    error_message = "TEAM_ID doit valoir g suivi de deux chiffres — g01, g02, …"
  }
}

variable "region" {
  description = "Région AWS. Doit être identique à celle du socle."
  type        = string
  default     = "eu-west-3"
}

variable "project" {
  description = "Valeur du tag Project."
  type        = string
  default     = "aws-ds"
}

variable "owner_email" {
  description = "Valeur du tag Owner. Adresse d'un membre du binôme."
  type        = string
}

variable "state_bucket" {
  description = <<-EOT
    Bucket d'état, pour lire les sorties du socle. Même valeur que dans `backend.hcl`.

    Le module a besoin de cette information DEUX fois : le backend l'utilise pour écrire
    son propre état, et `terraform_remote_state` pour lire celui du socle. Un bloc backend
    n'acceptant aucune variable, il n'est pas possible de les factoriser.
  EOT
  type        = string
}

variable "image_tag" {
  description = <<-EOT
    Tag de l'image dans ECR. `latest` convient pendant les labs.

    En production, on épinglerait le SHA du digest : un tag mobile signifie qu'on ne sait
    pas quelle version tourne réellement, et un redéploiement peut changer le code sans
    changer la configuration. C'est un sujet de discussion du J2.
  EOT
  type        = string
  default     = "latest"
}

variable "desired_count" {
  description = "Nombre de tâches. Modifié au J3 pour observer l'effet sur l'ALB et la facture."
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Unités de CPU. 512 = 0,5 vCPU. Fargate n'accepte que certaines combinaisons cpu/mémoire."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Mémoire en Mo. 1024 est le minimum autorisé avec 512 unités de CPU."
  type        = number
  default     = 1024
}

variable "app_port" {
  description = "Port du conteneur. 8501 est la valeur par défaut de Streamlit."
  type        = number
  default     = 8501
}

variable "log_retention_days" {
  description = "Rétention du groupe de logs de l'application. Modifiée au J3."
  type        = number
  default     = 7
}

variable "bedrock_model_id" {
  description = "Modèle Bedrock passé à l'application. Doit correspondre à celui autorisé par le rôle applicatif."
  type        = string
  default     = "mistral.mistral-large-2402-v1:0"
}
