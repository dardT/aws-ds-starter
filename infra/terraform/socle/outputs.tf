// Sorties obligatoires (§14) : elles alimentent directement le .env des binômes via
// `make env-from-tf`. Aucune valeur ne doit être recopiée à la main — une faute de frappe
// dans un ARN se diagnostique très mal.
//
// Tranches 1 et 2. La sortie du J3 (mlflow_tracking_uri) arrivera avec la tranche 3.

output "region" {
  description = "Région du socle. Doit correspondre à AWS_REGION du .env."
  value       = var.region
}

output "account_suffix" {
  description = "Suffixe de compte utilisé dans les noms de bucket. Doit correspondre au calcul de qc/config.py."
  value       = local.account_suffix
}

output "teams" {
  description = "Binômes provisionnés."
  value       = var.teams
}

output "s3_bucket_name" {
  description = "Bucket de données, par binôme. Alimente S3_BUCKET."
  value       = { for t, b in aws_s3_bucket.team : t => b.id }
}

output "sagemaker_execution_role_arn" {
  description = "Rôle d'exécution SageMaker, par binôme. Alimente SAGEMAKER_ROLE_ARN."
  value       = { for t, r in aws_iam_role.sagemaker_exec : t => r.arn }
}

output "log_group_names" {
  description = "Groupes de logs précréés, par binôme."
  value       = { for t, g in aws_cloudwatch_log_group.endpoint : t => g.name }
}

// =====================================================================================
// Tranche 2 — réseau, exposition, conteneurs
// =====================================================================================

output "vpc_id" {
  description = "VPC de la promotion."
  value       = aws_vpc.promo.id
}

output "public_subnet_ids" {
  description = "Sous-réseaux publics - ALB et passerelle NAT."
  value       = [for s in aws_subnet.public : s.id]
}

output "private_subnet_ids" {
  description = "Sous-réseaux privés - tâches ECS et machines de travail. Alimente le module team."
  value       = [for s in aws_subnet.private : s.id]
}

output "alb_dns_name" {
  description = "Nom DNS de l'ALB partagé. Alimente ALB_DNS_NAME."
  value       = aws_lb.promo.dns_name
}

output "alb_listener_arn" {
  description = <<-EOT
    Écouteur :80 de l'ALB partagé. C'est LA sortie dont le module `team/` a besoin : il y
    greffe sa règle `/<TEAM_ID>/*` sans jamais créer d'ALB lui-même.
  EOT
  value       = aws_lb_listener.http.arn
}

output "target_group_arns" {
  description = <<-EOT
    Groupe de cibles précréé, par binôme. C'est LA sortie que le module `team/` consomme
    pour accrocher son service ECS — il ne crée plus rien sur l'ALB lui-même (revue du
    29/07/2026, point 2).
  EOT
  value       = { for t, tg in aws_lb_target_group.team : t => tg.arn }
}

output "target_group_arn_suffixes" {
  description = <<-EOT
    Forme COURTE de l'ARN du groupe de cibles, par binôme — celle qu'attendent les
    dimensions CloudWatch (même piège que alb_arn_suffix). Sert au lab alarme du J3.
  EOT
  value       = { for t, tg in aws_lb_target_group.team : t => tg.arn_suffix }
}

output "alb_arn_suffix" {
  description = <<-EOT
    Forme COURTE de l'ARN de l'ALB, attendue par les dimensions CloudWatch.

    Les alarmes sur AWS/ApplicationELB n'acceptent pas l'ARN complet : avec lui, l'alarme
    est créée, reste en INSUFFICIENT_DATA pour toujours, et rien ne signale l'erreur.
    C'est le lab Terraform du J3.
  EOT
  value       = aws_lb.promo.arn_suffix
}

output "alb_security_group_id" {
  description = "Groupe de sécurité de l'ALB. Les tâches n'acceptent de trafic que de lui."
  value       = aws_security_group.alb.id
}

output "ecs_tasks_security_group_id" {
  description = "Groupe de sécurité des tâches ECS. Alimente le module team."
  value       = aws_security_group.ecs_tasks.id
}

output "ecs_cluster_name" {
  description = "Cluster ECS, par binôme. Alimente ECS_CLUSTER."
  value       = { for t, c in aws_ecs_cluster.team : t => c.name }
}

output "ecr_repository_url" {
  description = "Dépôt ECR, par binôme. Alimente ECR_REPO - c'est l'URL que `docker push` attend."
  value       = { for t, r in aws_ecr_repository.team : t => r.repository_url }
}

output "ecs_execution_role_arn" {
  description = "Rôle d'exécution de tâche, par binôme - tirer l'image, écrire les logs."
  value       = { for t, r in aws_iam_role.ecs_exec : t => r.arn }
}

output "ecs_task_role_arn" {
  description = "Rôle applicatif, par binôme - endpoint SageMaker et modèle Bedrock."
  value       = { for t, r in aws_iam_role.ecs_task : t => r.arn }
}

output "workstation_instance_ids" {
  description = "Machines de travail, par binôme. Vide tant que create_workstations vaut false."
  value       = { for t, i in aws_instance.workstation : t => i.id }
}

output "ide_alb_dns_name" {
  description = <<-EOT
    Nom DNS de la passerelle IDE navigateur (ide.tf). Vide tant que create_ide_gateway
    vaut false.

    C'est LA valeur à placer dans l'enregistrement CNAME de `ide_domain_name` chez le
    registrar (Hostinger) — `ide_domain_name` est un sous-domaine (`ide.vsc0de.fr`), pas
    l'apex de la zone, donc un simple CNAME suffit, sans ALIAS/ANAME (décision D29).
    Étape MANUELLE, hors du champ de Terraform — la zone DNS n'est pas hébergée dans ce
    compte AWS (décision D28).

    Les binômes, eux, ne voient jamais ce nom : ils vont sur
    `https://<ide_domain_name>/<TEAM_ID>/` (voir `make ide-credentials`).
  EOT
  value       = try(aws_lb.ide[0].dns_name, "")
}

output "ide_domain_name" {
  description = <<-EOT
    Domaine public de la passerelle IDE (variable `ide_domain_name`), renvoyé tel quel.

    Sortie plutôt que valeur recopiée : `make ide-credentials` et `make ide-check`
    construisent leurs URL à partir d'elle, comme tout le reste du socle lit ses noms
    dans les sorties Terraform et jamais dans la console.
  EOT
  value       = var.create_ide_gateway ? var.ide_domain_name : ""
}

output "ide_certificate_validation_records" {
  description = <<-EOT
    Enregistrements DNS que ACM attend pour valider le certificat de la passerelle IDE,
    à créer À LA MAIN dans le panneau DNS du registrar (Hostinger) : la zone n'est pas
    déléguée à Route 53, Terraform ne peut donc rien y écrire (décision D28).

    Tant qu'ils n'existent pas, `terraform apply` reste bloqué sur
    `aws_acm_certificate_validation.ide` — c'est attendu, pas une panne. Lancer un
    premier apply, lire cette sortie, créer les CNAME, relancer.
  EOT
  value = var.create_ide_gateway ? [
    for o in aws_acm_certificate.ide[0].domain_validation_options : {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  ] : []
}

output "workstation_profile_names" {
  description = <<-EOT
    Profils d'instance, par binôme. C'est eux qui portent l'isolation entre groupes
    (décision D21) : aucune clé statique n'est distribuée.
  EOT
  value       = { for t, p in aws_iam_instance_profile.workstation : t => p.name }
}

// Sortie de confort : le contenu prêt à coller dans le .env d'un binôme. Réduit le
// risque de recopie manuelle le matin du J1, quand tout le monde démarre en même temps.
output "env_fragment" {
  description = "Extrait de .env par binôme. Utilisé par `make env-from-tf`."
  value = {
    for t in var.teams : t => join("\n", [
      "TEAM_ID=${t}",
      // Le bucket d'état appartient au module bootstrap, pas au socle : on le
      // re-dérive du compte appelant, même règle que bootstrap/main.tf.
      "TF_STATE_BUCKET=qc-promo-tfstate-${local.account_suffix}",
      "S3_BUCKET=${aws_s3_bucket.team[t].id}",
      "SAGEMAKER_ROLE_ARN=${aws_iam_role.sagemaker_exec[t].arn}",
      "ECR_REPO=${aws_ecr_repository.team[t].repository_url}",
      "ECS_CLUSTER=${aws_ecs_cluster.team[t].name}",
      "ECS_SERVICE=qc-${t}-app",
      "ALB_DNS_NAME=${aws_lb.promo.dns_name}",
      // Vide tant que create_mlflow vaut false. Reporter la ligne quand même : le J3
      // échouerait sinon sur une clé absente plutôt que sur une valeur vide, et le
      // message serait moins clair.
      "MLFLOW_TRACKING_URI=${try(aws_sagemaker_mlflow_tracking_server.promo[0].arn, "")}",
      // Dérivé de `var.teams` et non saisi à la main : le preflight en tire le besoin de
      // quota SageMaker, et un effectif qui change dans Terraform doit se propager
      // (décision D1).
      "TEAMS_COUNT=${length(var.teams)}",
    ])
  }
}

// =====================================================================================
// Tranche 3 — MLflow
// =====================================================================================

output "mlflow_tracking_uri" {
  description = <<-EOT
    ARN du serveur de suivi MLflow. Alimente MLFLOW_TRACKING_URI.

    C'est bien un ARN et non une URL : le client `sagemaker-mlflow` (décision D8) le
    résout lui-même. Passer une URL https produit une erreur d'authentification peu
    parlante.

    Vide tant que `create_mlflow` vaut false.
  EOT
  value       = try(aws_sagemaker_mlflow_tracking_server.promo[0].arn, "")
}

output "alarmes_topic_arn" {
  description = "Sujet SNS des alarmes. Aucun abonné : le J3 observe les transitions dans la console."
  value       = aws_sns_topic.alarmes.arn
}
