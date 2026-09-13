variable "region" {
  description = "Région AWS. Doit être identique à AWS_REGION du .env (§12)."
  type        = string
  default     = "eu-west-3"
}

variable "project" {
  description = "Valeur du tag Project, imposée par §11."
  type        = string
  default     = "aws-ds"
}

variable "owner_email" {
  description = "Valeur du tag Owner. Adresse du formateur."
  type        = string
}
