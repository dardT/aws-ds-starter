// Alarmes CloudWatch — tranche 3 du socle.
//
// Trois alarmes par groupe, chacune sur un mode de défaillance différent. Elles sont
// créées ici, à vide, pour que le J3 dispose d'un tableau de bord dès l'ouverture plutôt
// que d'avoir à tout construire avant de pouvoir observer quoi que ce soit.
//
// Une alarme coûte quelques centimes par mois. Contrairement au reste de la tranche 3,
// elles ne sont donc pas conditionnées.
//
// Ce que le J3 ajoute par-dessus, dans le module `team/` : une quatrième alarme, écrite
// par le binôme, sur une métrique qu'il choisit. C'est le lab Terraform.

// Sujet SNS commun. Sans destinataire abonné, une alarme change d'état sans prévenir
// personne — ce qui est exactement le comportement voulu ici : le J3 observe les
// transitions dans la console, il n'envoie pas de courriels à quinze personnes.
resource "aws_sns_topic" "alarmes" {
  name = "qc-promo-alarmes"
}

// --- 1. L'endpoint renvoie des erreurs -------------------------------------------------
//
// Invocation4XXErrors compte les requêtes rejetées par le conteneur : mauvais nombre de
// colonnes, corps illisible. C'est la première alarme à se déclencher quand un binôme
// modifie son code d'invocation.
resource "aws_cloudwatch_metric_alarm" "endpoint_erreurs" {
  for_each = toset(var.teams)

  alarm_name          = "qc-${each.key}-endpoint-erreurs"
  alarm_description   = "L'endpoint rejette des requêtes — format d'entrée le plus souvent."
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation4XXErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  // Sans données, l'alarme reste en INSUFFICIENT_DATA plutôt que de passer en ALARM.
  // Un endpoint éteint le soir ne doit pas déclencher une alerte à chaque nuit.
  treat_missing_data = "notBreaching"

  dimensions = {
    EndpointName = "qc-${each.key}-endpoint"
    VariantName  = "AllTraffic"
  }

  alarm_actions = [aws_sns_topic.alarmes.arn]
  ok_actions    = [aws_sns_topic.alarmes.arn]

  tags = {
    Team = each.key
  }
}

// --- 2. L'endpoint devient lent ---------------------------------------------------------
//
// ModelLatency est mesurée en MICROsecondes. 1 000 000 = 1 seconde. C'est une source
// d'erreur classique : un seuil écrit en millisecondes déclenche l'alarme en permanence.
resource "aws_cloudwatch_metric_alarm" "endpoint_latence" {
  for_each = toset(var.teams)

  alarm_name          = "qc-${each.key}-endpoint-latence"
  alarm_description   = "Latence du modèle au-dessus d'une seconde sur deux périodes."
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.latence_max_microsecondes
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    EndpointName = "qc-${each.key}-endpoint"
    VariantName  = "AllTraffic"
  }

  alarm_actions = [aws_sns_topic.alarmes.arn]

  tags = {
    Team = each.key
  }
}

// --- 3. L'application n'a plus de tâche en service --------------------------------------
//
// RunningTaskCount à zéro signifie qu'ECS relance une tâche en boucle, ou que le service a
// été mis à zéro. Le symptôme côté utilisateur est un 503 sur l'ALB, qui ne dit pas
// pourquoi.
resource "aws_cloudwatch_metric_alarm" "service_sans_tache" {
  for_each = toset(var.teams)

  alarm_name        = "qc-${each.key}-service-sans-tache"
  alarm_description = "Plus aucune tâche en service — redémarrage en boucle ou service arrêté."
  namespace         = "ECS/ContainerInsights"
  metric_name       = "RunningTaskCount"

  // MAXIMUM et non Average. Container Insights publie une valeur moyennée sur la
  // période : pendant le démarrage d'une tâche, elle vaut 0,75 alors qu'une tâche tourne
  // bel et bien à la fin de la période. Avec Average, l'alarme se déclenche à chaque
  // déploiement et reste rouge — constaté le 28/07/2026 au premier team-apply.
  //
  // Avec Maximum, elle ne se déclenche que si AUCUNE tâche n'a tourné de toute la
  // période, ce qui est exactement ce qu'on veut dire par « plus aucune tâche en
  // service ».
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"

  // `breaching` et non `missing` : ici l'absence de données EST le symptôme. Un service
  // détruit cesse d'émettre — or avec `missing`, CloudWatch exclut les points absents de
  // l'évaluation et l'alarme reste en INSUFFICIENT_DATA pour toujours : elle ne peut pas
  // basculer en ALARM, exactement dans le cas qu'elle prétend couvrir. Seul `breaching`
  // transforme le silence en ALARM.
  //
  // Contrepartie assumée : l'extinction du soir (make destroy) déclenche une fausse
  // alarme par groupe. La variable permet de repasser à `missing` en mode formation.
  treat_missing_data = var.alarm_treat_missing_data

  dimensions = {
    ClusterName = "qc-${each.key}-cluster"
    ServiceName = "qc-${each.key}-app"
  }

  alarm_actions = [aws_sns_topic.alarmes.arn]

  tags = {
    Team = each.key
  }
}
