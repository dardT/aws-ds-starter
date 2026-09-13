// Groupes de logs précréés (§14).
//
// Motif : le J3 doit disposer immédiatement de logs exploitables. Si le groupe de logs
// n'est créé qu'au premier appel de l'endpoint, un binôme qui ouvre CloudWatch Logs
// Insights au matin du J3 tombe sur « aucun groupe de logs » et croit à une erreur.
//
// Les créer ici fixe aussi la rétention. Par défaut, un groupe créé automatiquement par
// un service AWS a une rétention infinie — donc une facturation qui ne s'arrête jamais.

resource "aws_cloudwatch_log_group" "endpoint" {
  for_each = toset(var.teams)

  name              = "/aws/sagemaker/Endpoints/qc-${each.key}-endpoint"
  retention_in_days = var.log_retention_days

  tags = {
    Team = each.key
  }
}

// Le groupe de logs de l'application conteneurisée, /ecs/qc-<TEAM_ID>-app, n'est PAS créé
// ici : il appartient au module `team/`. Le lab Terraform du J3 consiste notamment à en
// changer la rétention, et les apprenants n'ont la main que sur `team/`.
//
// Le rôle d'exécution de tâche (voir iam.tf) l'autorise par son ARN construit, sans
// dépendre de la ressource : le socle s'applique avant que le groupe n'existe.
