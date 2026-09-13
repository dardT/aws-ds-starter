// Rôle d'exécution SageMaker, un par binôme (§14).
//
// C'est SageMaker qui l'endosse, pas l'apprenant : quand un training job démarre, le
// service prend ce rôle pour lire les données, écrire l'artefact et journaliser. C'est la
// distinction que la masterclass 2 du J1 doit rendre limpide, et la raison pour laquelle
// la politique de confiance nomme sagemaker.amazonaws.com et personne d'autre.
//
// Toute la politique est scopée au bucket du groupe. Un binôme ne peut donc ni lire, ni
// écrire, ni détruire les données d'un autre — ce qui rend la démonstration du privilège
// minimal concrète plutôt que théorique.

data "aws_iam_policy_document" "sagemaker_trust" {
  statement {
    sid     = "SageMakerPeutEndosserCeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }

    // Empêche le « confused deputy » : le rôle n'est endossable que pour des ressources
    // de CE compte, pas pour celles d'un tiers qui connaîtrait son ARN.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "sagemaker_exec" {
  for_each = toset(var.teams)

  name               = "qc-${each.key}-sagemaker-exec"
  description        = "Rôle d'exécution SageMaker du binôme ${each.key}. Scopé à son seul bucket."
  assume_role_policy = data.aws_iam_policy_document.sagemaker_trust.json

  tags = {
    Team = each.key
  }
}

data "aws_iam_policy_document" "sagemaker_exec" {
  for_each = local.team_buckets

  // --- Données du groupe, et rien d'autre -----------------------------------------
  statement {
    sid    = "LireEtEcrireSonPropreBucket"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [
      "arn:aws:s3:::${each.value}",
      "arn:aws:s3:::${each.value}/*",
    ]
  }

  // --- Conteneurs d'inférence et d'entraînement ------------------------------------
  // SageMaker tire ses images depuis des dépôts ECR gérés par AWS. Le rôle a donc besoin
  // de LIRE ECR, mais jamais d'y écrire : pousser une image est le travail du binôme au
  // J2, pas celui du service.
  statement {
    sid    = "LireLesImagesDInference"
    effect = "Allow"

    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]

    resources = ["*"] // lectures seules sur les dépôts ECR gérés par AWS, dont l'ARN n'est pas connu à l'avance
  }

  // --- Journalisation ---------------------------------------------------------------
  // Sans ces droits, un training job qui échoue ne laisse aucune trace exploitable : on
  // se retrouve avec un statut « Failed » et rien pour comprendre. C'est aussi ce qui
  // alimente le J3.
  statement {
    sid    = "EcrireSesLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]

    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/*"]
  }

  // --- Métriques --------------------------------------------------------------------
  // PutMetricData n'accepte pas de restriction par ressource ; on borne donc par
  // l'espace de noms, ce qui est la seule condition disponible sur cette action.
  statement {
    sid       = "PublierSesMetriques"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] // PutMetricData ne se restreint que par la condition cloudwatch:namespace ci-dessous

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/TrainingJobs", "AWS/SageMaker"]
    }
  }
}

resource "aws_iam_role_policy" "sagemaker_exec" {
  for_each = local.team_buckets

  name   = "qc-${each.key}-sagemaker-exec"
  role   = aws_iam_role.sagemaker_exec[each.key].id
  policy = data.aws_iam_policy_document.sagemaker_exec[each.key].json
}

// =====================================================================================
// Tranche 2 — rôles ECS et profil d'instance des machines de travail
// =====================================================================================

// --- Rôle d'exécution de tâche : qc-<TEAM_ID>-ecs-exec --------------------------------
//
// C'est l'agent ECS qui l'endosse, AVANT que le conteneur ne démarre, pour tirer l'image
// et ouvrir le flux de logs. Il n'a besoin de rien d'autre. La distinction avec le rôle
// applicatif ci-dessous est un objectif pédagogique explicite du J2 : ce rôle-ci sert à
// DÉMARRER la tâche, l'autre à la faire TRAVAILLER.

data "aws_iam_policy_document" "ecs_trust" {
  statement {
    sid     = "ECSPeutEndosserCeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "ecs_exec" {
  for_each = toset(var.teams)

  name               = "qc-${each.key}-ecs-exec"
  description        = "Rôle d'exécution de tâche ECS du binôme ${each.key} - tirer l'image, écrire les logs."
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json

  tags = {
    Team = each.key
  }
}

data "aws_iam_policy_document" "ecs_exec" {
  for_each = toset(var.teams)

  statement {
    sid       = "ObtenirUnJetonECR"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] // cette action n'accepte aucune restriction par ressource
  }

  statement {
    sid    = "TirerSaPropreImage"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]

    resources = [aws_ecr_repository.team[each.key].arn]
  }

  statement {
    sid    = "EcrireLesLogsDeLaTache"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    // ARN construit et non référence de ressource : le groupe est créé par le module
    // `team/`, qui s'applique après le socle.
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/qc-${each.key}-app:*",
    ]
  }
}

resource "aws_iam_role_policy" "ecs_exec" {
  for_each = toset(var.teams)

  name   = "qc-${each.key}-ecs-exec"
  role   = aws_iam_role.ecs_exec[each.key].id
  policy = data.aws_iam_policy_document.ecs_exec[each.key].json
}

// --- Rôle applicatif : qc-<TEAM_ID>-ecs-task ------------------------------------------
//
// Celui-ci est endossé par le CODE qui tourne dans le conteneur. C'est lui qui autorise
// l'agent du J2 à interroger l'endpoint SageMaker et le modèle Bedrock.
//
// Un binôme qui confond les deux obtient une tâche qui démarre parfaitement et qui échoue
// à la première prédiction, avec un AccessDenied dans les logs applicatifs et non dans
// ceux d'ECS.

resource "aws_iam_role" "ecs_task" {
  for_each = toset(var.teams)

  name               = "qc-${each.key}-ecs-task"
  description        = "Rôle applicatif du binôme ${each.key} - endpoint SageMaker et modèle Bedrock."
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json

  tags = {
    Team = each.key
  }
}

data "aws_iam_policy_document" "ecs_task" {
  for_each = local.team_buckets

  statement {
    sid       = "InvoquerSonSeulEndpoint"
    effect    = "Allow"
    actions   = ["sagemaker:InvokeEndpoint"]
    resources = ["arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:endpoint/qc-${each.key}-endpoint"]
  }

  // Borné au seul modèle réellement activé dans le compte (décision D14). Ouvrir
  // `bedrock:InvokeModel` sur `*` donnerait accès à tout le catalogue, y compris aux
  // modèles dont l'accès n'est pas accordé — l'erreur serait alors incompréhensible.
  statement {
    sid    = "AppelerLeSeulModeleAutorise"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]

    // Deux formes d'ARN : le modèle brut et le profil d'inférence régional. Les deux
    // coexistent en Europe et certains modèles ne sont appelables que par la seconde.
    resources = [
      "arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}",
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/*",
    ]
  }

  // Le J3 lit la data capture pour comparer la production à la baseline.
  statement {
    sid    = "LireSesDonneesEtSaCapture"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]

    resources = [
      "arn:aws:s3:::${each.value}",
      "arn:aws:s3:::${each.value}/*",
    ]
  }

  // L'outil `expliquer_le_score` a besoin du MODELE, pas seulement de l'endpoint :
  // l'endpoint renvoie un score, il ne decompose rien. Le module `qc.explain` retrouve
  // donc l'artefact du dernier entrainement reussi, ce qui demande deux appels a l'API
  // SageMaker en plus du `GetObject` deja accorde ci-dessus.
  // ListTrainingJobs n'accepte pas de restriction par ressource.
  statement {
    sid       = "RetrouverArtefactDuModele"
    effect    = "Allow"
    actions   = ["sagemaker:ListTrainingJobs"]
    resources = ["*"] // ListTrainingJobs n'accepte aucune restriction par ressource
  }

  statement {
    sid       = "DecrireSesPropresEntrainements"
    effect    = "Allow"
    actions   = ["sagemaker:DescribeTrainingJob"]
    resources = ["arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:training-job/qc-${each.key}-*"]
  }

  // ECS Exec : le service est créé avec `enable_execute_command`, mais sans ces quatre
  // actions l'agent Exec démarre (RUNNING dans describe-tasks) et reste sourd — tout
  // start-session échoue en TargetNotConnected. C'est le canal SSM du formateur vers
  // l'ALB interne quand aucune workstation ne tourne.
  statement {
    sid    = "OuvrirLeCanalExecSSM"
    effect = "Allow"

    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]

    resources = ["*"] // les canaux ssmmessages n'ont pas d'ARN restreignable
  }

  // Tableau de bord et diagnostic du J3, depuis l'application elle-même.
  statement {
    sid    = "LireSesMetriquesEtSesLogs"
    effect = "Allow"

    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "logs:FilterLogEvents",
      "logs:DescribeLogStreams",
    ]

    resources = ["*"] // GetMetricData n'accepte pas de restriction par ressource
  }
}

resource "aws_iam_role_policy" "ecs_task" {
  for_each = local.team_buckets

  name   = "qc-${each.key}-ecs-task"
  role   = aws_iam_role.ecs_task[each.key].id
  policy = data.aws_iam_policy_document.ecs_task[each.key].json
}
