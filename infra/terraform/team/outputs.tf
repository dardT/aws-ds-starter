output "app_url" {
  description = "Adresse de l'application. Joignable depuis la machine de travail, l'ALB étant interne."
  value       = "http://${local.socle.alb_dns_name}/${var.team_id}/"
}

output "target_group_arn" {
  description = "Groupe de cibles du binôme — précréé par le socle, consommé ici."
  value       = local.socle.target_group_arns[var.team_id]
}

output "ecs_service_name" {
  description = "Service ECS. Alimente ECS_SERVICE."
  value       = aws_ecs_service.app.name
}

output "task_definition_family" {
  description = "Famille de la définition de tâche."
  value       = aws_ecs_task_definition.app.family
}

output "log_group_name" {
  description = "Groupe de logs de l'application. C'est là que le J3 cherche les erreurs."
  value       = aws_cloudwatch_log_group.app.name
}
