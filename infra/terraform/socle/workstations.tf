// Machines de travail et isolation entre groupes — tranche 2 du socle.
//
// C'est ici que se règle la décision D21. Jusqu'à la tranche 1, le socle scopait bien une
// politique par groupe — mais sur `qc-<TEAM_ID>-sagemaker-exec`, le rôle que SAGEMAKER
// endosse. Rien ne contraignait l'identité depuis laquelle l'APPRENANT agit. Un binôme
// qui se trompait de valeur dans `S3_BUCKET` écrasait les données d'un autre sans
// rencontrer le moindre refus, et le lab « accès refusé » du J1 ne pouvait pas
// fonctionner puisque la lecture réussissait.
//
// Le profil d'instance ci-dessous est ce qui manquait. Aucune clé statique n'est
// distribuée : la machine porte le profil, la chaîne de résolution boto3 par défaut le
// trouve seule, et toutes les permissions du binôme en découlent.
//
// Les machines sont définies dans le socle et non dans `team/` (décision D2) : les
// apprenants n'ont la main que sur `team/`, ils ne peuvent donc pas détruire leur propre
// machine avec le `terraform destroy` du J3.

// --- Rôle et profil d'instance --------------------------------------------------------

data "aws_iam_policy_document" "workstation_trust" {
  statement {
    sid     = "EC2PeutEndosserCeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "workstation" {
  for_each = toset(var.teams)

  name               = "qc-${each.key}-workstation"
  description        = "Identité de la machine de travail du binôme ${each.key}. Scopée à ses seules ressources."
  assume_role_policy = data.aws_iam_policy_document.workstation_trust.json

  tags = {
    Team = each.key
  }
}

// Session Manager est le SEUL accès aux machines (décision D2) : pas de clé SSH, pas de
// port entrant ouvert. Sans cette politique, les machines démarrent et restent
// injoignables — panne bloquante au matin du J1, et invisible avant.
resource "aws_iam_role_policy_attachment" "workstation_ssm" {
  for_each = aws_iam_role.workstation

  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "workstation" {
  for_each = aws_iam_role.workstation

  name = "qc-${each.key}-workstation-profile"
  role = each.value.name

  tags = {
    Team = each.key
  }
}

// --- La politique du binôme -----------------------------------------------------------
//
// Tout est scopé au préfixe `qc-<TEAM_ID>`. Ce qui n'est pas explicitement autorisé ici
// est refusé : c'est ce refus implicite qui fait échouer la lecture du bucket d'un autre
// groupe, et qui rend le lab du J1 démontrable.
//
// Certaines actions n'acceptent aucune restriction par ressource — lister des jobs,
// obtenir un jeton ECR, lire des métriques. Elles sont regroupées à part et commentées,
// parce qu'un apprenant qui lit cette politique doit comprendre POURQUOI elles sont
// ouvertes plutôt que d'y voir un relâchement.

data "aws_iam_policy_document" "workstation" {
  for_each = local.team_buckets

  // --- S3 : son bucket, et lui seul ---------------------------------------------------
  statement {
    sid    = "SonPropreBucket"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetObjectTagging",
      "s3:PutObjectTagging",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:GetEncryptionConfiguration",
      "s3:GetBucketPublicAccessBlock",
      // Le lab IAM du J1 fait LIRE les garde-fous du bucket, politique comprise
      // (exercice 2.3.2 du cours) — lecture seule, sur son seul bucket.
      "s3:GetBucketPolicy",
    ]

    resources = [
      "arn:aws:s3:::${each.value}",
      "arn:aws:s3:::${each.value}/*",
    ]
  }

  // --- CloudWatch : ses alarmes et dashboards d'exercice ------------------------------
  // Le J3 fait CRÉER puis SUPPRIMER une alarme et un dashboard (exercices du cours).
  // Écriture bornée au préfixe du groupe : impossible de toucher aux alarmes du socle
  // des autres équipes ou aux dashboards voisins.
  statement {
    sid    = "SesAlarmesDExercice"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
    ]

    resources = ["arn:aws:cloudwatch:*:${data.aws_caller_identity.current.account_id}:alarm:qc-${each.key}-*"]
  }

  statement {
    sid    = "SesDashboardsDExercice"
    effect = "Allow"

    actions = [
      "cloudwatch:PutDashboard",
      "cloudwatch:DeleteDashboards",
      "cloudwatch:GetDashboard",
    ]

    resources = ["arn:aws:cloudwatch::${data.aws_caller_identity.current.account_id}:dashboard/qc-${each.key}*"]
  }

  // Lectures CloudWatch : ces actions n'acceptent pas de restriction par ressource
  // (même limite que PutMetricData, documentée dans iam.tf) — lecture seule.
  statement {
    sid    = "LireMetriquesEtAlarmes"
    effect = "Allow"

    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "cloudwatch:ListDashboards",
    ]

    resources = ["*"] // ces lectures CloudWatch n'acceptent aucune restriction par ressource
  }

  // --- Logs : lire les siens ----------------------------------------------------------
  // Diagnostics du J2/J3 : logs de la tâche ECS, logs de l'endpoint, Logs Insights.
  // `DescribeLogGroups` n'accepte pas de restriction utile par ressource ; les lectures
  // de contenu, elles, sont bornées aux groupes du binôme.
  statement {
    sid       = "ListerLesGroupesDeLogs"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"] // DescribeLogGroups n'accepte pas de restriction utile par ressource
  }

  statement {
    sid    = "LireSesLogs"
    effect = "Allow"

    actions = [
      "logs:DescribeLogStreams",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
      "logs:StartQuery",
      "logs:GetQueryResults",
    ]

    resources = [
      "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/ecs/qc-${each.key}-*",
      "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/ecs/qc-${each.key}-*:*",
      "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/*",
      "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/*:*",
    ]
  }

  // --- État Terraform : sa propre clé -------------------------------------------------
  // Le lab Terraform du J3 applique le module `team/`, dont l'état vit dans le bucket
  // commun sous `team/<TEAM_ID>/`. Sans cet accès, `terraform init` échoue. Avec un accès
  // au bucket entier, un binôme pourrait corrompre l'état du socle.
  statement {
    sid    = "SonPropreEtatTerraform"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]

    resources = [
      "arn:aws:s3:::qc-promo-tfstate-${local.account_suffix}/team/${each.key}/*",
    ]
  }

  // Le module `team/` lit les sorties du socle (ARN de l'écouteur, sous-réseaux) par un
  // `terraform_remote_state`. Sans cette lecture, `terraform init` échoue. En LECTURE
  // seule : l'état du socle ne doit jamais être écrasé par un binôme.
  statement {
    sid       = "LireLEtatDuSocle"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::qc-promo-tfstate-${local.account_suffix}/socle/terraform.tfstate"]
  }

  statement {
    sid       = "ListerLeBucketDEtatPourSonPrefixe"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::qc-promo-tfstate-${local.account_suffix}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["team/${each.key}/*", "socle/*"]
    }
  }

  // --- SageMaker : ses propres ressources ----------------------------------------------
  statement {
    sid    = "SesPropresRessourcesSageMaker"
    effect = "Allow"

    actions = [
      "sagemaker:CreateTrainingJob",
      "sagemaker:DescribeTrainingJob",
      "sagemaker:StopTrainingJob",
      "sagemaker:CreateModel",
      "sagemaker:DescribeModel",
      "sagemaker:DeleteModel",
      "sagemaker:CreateEndpointConfig",
      "sagemaker:DescribeEndpointConfig",
      "sagemaker:DeleteEndpointConfig",
      "sagemaker:CreateEndpoint",
      "sagemaker:UpdateEndpoint",
      "sagemaker:DescribeEndpoint",
      "sagemaker:DeleteEndpoint",
      "sagemaker:InvokeEndpoint",
      "sagemaker:AddTags",
      "sagemaker:ListTags",
    ]

    resources = [
      "arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:training-job/qc-${each.key}-*",
      "arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:model/qc-${each.key}-*",
      "arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:endpoint-config/qc-${each.key}-*",
      "arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:endpoint/qc-${each.key}-*",
    ]
  }

  // --- Passer les rôles, et seulement les siens -----------------------------------------
  // `iam:PassRole` est le droit de confier un rôle à un service. Sans lui,
  // `CreateTrainingJob` échoue en refusant le rôle d'exécution — l'erreur la plus
  // déroutante du J1, parce qu'elle parle du rôle et non du droit de le passer.
  // La condition empêche de passer ces rôles à un autre service que celui prévu.
  statement {
    sid     = "PasserSesPropresRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]

    resources = [
      aws_iam_role.sagemaker_exec[each.key].arn,
      aws_iam_role.ecs_exec[each.key].arn,
      aws_iam_role.ecs_task[each.key].arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com", "ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "LireSesPropresRoles"
    effect    = "Allow"
    actions   = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/qc-${each.key}-*"]
  }

  // --- ECR : son propre dépôt ------------------------------------------------------------
  statement {
    sid    = "PousserDansSonPropreDepot"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:ListImages",
    ]

    resources = [aws_ecr_repository.team[each.key].arn]
  }

  // --- ECS : son propre cluster ----------------------------------------------------------
  statement {
    sid    = "SonPropreCluster"
    effect = "Allow"

    actions = [
      "ecs:CreateService",
      "ecs:UpdateService",
      "ecs:DeleteService",
      "ecs:DescribeServices",
      "ecs:ListServices",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
      "ecs:RunTask",
      "ecs:StopTask",
      "ecs:DescribeClusters",
      "ecs:TagResource",
    ]

    resources = [
      aws_ecs_cluster.team[each.key].arn,
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/qc-${each.key}-cluster/*",
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/qc-${each.key}-cluster/*",
      // RegisterTaskDefinition n'est pas restreignable (voir ActionsNonScopables), mais
      // le ecs:TagResource qu'il déclenche — les default_tags du provider s'appliquent à
      // l'enregistrement — vise l'ARN de la DÉFINITION. Sans cette ligne, team-apply
      // échoue depuis la workstation. Constaté le 10/08/2026.
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/qc-${each.key}-*",
    ]
  }

  // --- MLflow : tracer ses exécutions du J3 ------------------------------------------------
  //
  // Décision D8. Deux familles d'actions, et il faut les DEUX : `sagemaker:*MlflowTracking*`
  // pour dialoguer avec le serveur, et `sagemaker-mlflow:*` pour l'API MLflow elle-même,
  // qu'AWS expose sous un service distinct. N'accorder que la première donne un
  // `AccessDenied` au premier `mlflow.log_metric`, avec un message qui ne nomme ni l'une
  // ni l'autre.
  statement {
    sid    = "TracerDansMLflow"
    effect = "Allow"

    actions = [
      "sagemaker:DescribeMlflowTrackingServer",
      "sagemaker:CreatePresignedMlflowTrackingServerUrl",
      "sagemaker-mlflow:AccessUI",
      "sagemaker-mlflow:CreateExperiment",
      "sagemaker-mlflow:SearchExperiments",
      "sagemaker-mlflow:GetExperiment",
      // Le client mlflow résout d'abord l'expérience PAR NOM (get-by-name) : sans cette
      // action, le premier log_metric du J3 échoue en 403 — constaté le 10/08/2026
      // depuis une workstation réelle.
      "sagemaker-mlflow:GetExperimentByName",
      "sagemaker-mlflow:CreateRun",
      "sagemaker-mlflow:UpdateRun",
      "sagemaker-mlflow:GetRun",
      "sagemaker-mlflow:SearchRuns",
      "sagemaker-mlflow:LogMetric",
      "sagemaker-mlflow:LogParam",
      "sagemaker-mlflow:LogBatch",
      "sagemaker-mlflow:SetTag",
      "sagemaker-mlflow:GetLatestModelVersions",
      "sagemaker-mlflow:CreateModelVersion",
      "sagemaker-mlflow:CreateRegisteredModel",
    ]

    resources = [
      "arn:aws:sagemaker:${var.region}:${data.aws_caller_identity.current.account_id}:mlflow-tracking-server/qc-promo-mlflow",
    ]
  }

  // Le serveur MLflow écrit ses artefacts dans un bucket commun à la promotion. Sans cet
  // accès, `mlflow.log_artifact` sur le rapport de dérive échoue.
  statement {
    sid    = "ArtefactsMLflow"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]

    resources = [
      "arn:aws:s3:::qc-promo-mlflow-${local.account_suffix}",
      "arn:aws:s3:::qc-promo-mlflow-${local.account_suffix}/*",
    ]
  }

  // --- Bedrock : le seul modèle autorisé ---------------------------------------------------
  statement {
    sid    = "AppelerLeSeulModeleAutorise"
    effect = "Allow"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]

    resources = [
      "arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}",
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/*",
    ]
  }

  // --- Journalisation et métriques ----------------------------------------------------------
  statement {
    sid    = "SesPropresLogs"
    effect = "Allow"

    actions = [
      // DeleteLogGroup : le lab Terraform du J3 gère /ecs/qc-<TEAM_ID>-app dans le
      // module team — sans ce droit, `team-destroy` échoue sur le groupe de logs.
      // Constaté le 10/08/2026 au premier run depuis une vraie workstation.
      "logs:DeleteLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:PutRetentionPolicy",
      "logs:DescribeLogGroups",
      "logs:TagResource",
      // Le provider AWS relit les tags de chaque log group au refresh : sans ces deux
      // lectures, `terraform apply` du module team échoue sur « listing tags for
      // CloudWatch Logs Log Group ». Les deux formes couvrent l'ancienne et la
      // nouvelle API. Même constat du 10/08/2026.
      "logs:ListTagsForResource",
      "logs:ListTagsLogGroup",
    ]

    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/qc-${each.key}-*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/qc-${each.key}-*:*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/Endpoints/qc-${each.key}-*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/Endpoints/qc-${each.key}-*:*",
    ]
  }

  // AWS n'accepte pas le scopage par ARN pour CreateLogGroup. Le nom créé est
  // toutefois imposé par le module team et par les exercices ; l'action est
  // séparée afin de conserver les autres permissions de logs sur qc-<TEAM_ID>-*.
  statement {
    sid       = "CreerUnGroupeDeLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup"]
    resources = ["*"] // logs:CreateLogGroup n'accepte pas de restriction par ressource
  }

  // --- IDE navigateur : lire son propre mot de passe code-server -----------------------
  // Ajouté pour la passerelle IDE navigateur (ide.tf, PLAN.md). Le script poussé par
  // `aws_ssm_association.code_server_setup` tourne SUR la machine, avec ce rôle : sans
  // cette lecture, code-server démarrerait avec un mot de passe généré au hasard à
  // chaque démarrage au lieu de celui distribué par `make ide-credentials` (voir
  // ide_setup.sh.tftpl). Statement présent même quand create_ide_gateway vaut false —
  // une permission de lecture sur un paramètre qui n'existe pas encore est sans effet,
  // et ça évite un cycle de dépendance entre la politique et la passerelle.
  statement {
    sid       = "SonPropreMotDePasseCodeServer"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/qc/${each.key}/code-server-password"]
  }

  // --- Actions sans restriction possible par ressource ----------------------------------------
  //
  // AWS ne permet pas de scoper ces actions. Elles sont en LECTURE ou en découverte : un
  // binôme peut donc voir qu'un autre groupe existe, mais pas toucher à ses ressources —
  // toutes les actions d'écriture ci-dessus sont bornées à son propre préfixe.
  //
  // C'est un point de discussion du J1 : le privilège minimal n'est pas un absolu, il
  // s'arrête là où l'API du fournisseur s'arrête.
  statement {
    sid    = "ActionsNonScopables"
    effect = "Allow"

    actions = [
      "sagemaker:ListTrainingJobs",
      "sagemaker:ListModels",
      "sagemaker:ListEndpoints",
      "sagemaker:ListEndpointConfigs",
      "sagemaker:DescribeEndpoint",
      "ecr:GetAuthorizationToken",
      "ecs:RegisterTaskDefinition",
      "ecs:DeregisterTaskDefinition",
      "ecs:DescribeTaskDefinition",
      "ecs:ListTaskDefinitions",
      "ecs:ListClusters",
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "sts:GetCallerIdentity",
      "servicequotas:GetServiceQuota",
      "servicequotas:ListServiceQuotas",
      "ec2:DescribeRegions",
      "ec2:DescribeAvailabilityZones",
      // Lecture seule sur l'ALB : voir l'état de santé de SES cibles au J2/J3. Les
      // écritures elasticloadbalancing ont été retirées (revue du 29/07/2026, point 2) :
      // la règle et le groupe de cibles sont précréés par le socle (alb_teams.tf), et
      // CreateRule/DeleteRule sur l'écouteur partagé permettaient à un binôme de
      // supprimer la règle d'un autre.
      "elasticloadbalancing:Describe*",
    ]

    resources = ["*"] // que des List/Describe/GetToken : aucune de ces actions n'accepte de restriction par ressource
  }
}

resource "aws_iam_role_policy" "workstation" {
  for_each = local.team_buckets

  name   = "qc-${each.key}-workstation"
  role   = aws_iam_role.workstation[each.key].id
  policy = data.aws_iam_policy_document.workstation[each.key].json
}

// --- Les machines elles-mêmes ------------------------------------------------------------
//
// Créées seulement quand `create_workstations = true`. Six machines allumées pendant les
// semaines de préparation coûteraient bien plus cher que la formation elle-même : on
// applique le socle tôt, on allume les machines la veille.

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] // Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_security_group" "workstation" {
  name        = "qc-promo-workstation"
  description = "Machines de travail - aucune entree, acces par Session Manager"
  vpc_id      = aws_vpc.promo.id

  // AUCUNE règle d'entrée, volontairement. Session Manager fonctionne par une connexion
  // SORTANTE de l'agent SSM vers AWS : c'est ce qui permet d'ouvrir un shell sur une
  // machine sans lui ouvrir le moindre port, et depuis un poste verrouillé.

  egress {
    description = "Sortie vers les services AWS et internet, par la NAT"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "qc-promo-workstation"
  }
}

resource "aws_instance" "workstation" {
  // `workstation_teams` permet de n'allumer qu'un sous-ensemble des machines —
  // une seule pour une répétition générale, toutes (défaut) pour la formation.
  for_each = var.create_workstations ? toset(coalesce(var.workstation_teams, var.teams)) : toset([])

  // L'AMI dorée (décision D2) est construite et validée à l'avance, avec Docker, buildx,
  // uv, git et le dépôt starter précloné — pour éviter une installation devant quinze
  // personnes au matin du J1. Tant qu'elle n'existe pas, on retombe sur l'Ubuntu officiel
  // et le bootstrap doit être fait à la main.
  ami           = var.workstation_ami_id != "" ? var.workstation_ami_id : data.aws_ami.ubuntu.id
  instance_type = var.workstation_instance_type

  // Sous-réseau PRIVÉ : la machine sort par la NAT, rien ne peut l'atteindre depuis
  // internet. Elle reste joignable par Session Manager.
  subnet_id              = aws_subnet.private["a"].id
  vpc_security_group_ids = [aws_security_group.workstation.id]
  iam_instance_profile   = aws_iam_instance_profile.workstation[each.key].name

  root_block_device {
    volume_size = var.workstation_disk_gb
    volume_type = "gp3"
    encrypted   = true
  }

  // Le bootstrap n'est exécuté QUE si l'on démarre sur une AMI Ubuntu nue. Sur l'AMI
  // dorée, tout est déjà installé : le relancer coûterait plusieurs minutes de démarrage
  // par machine, pour rien.
  //
  // Il est idempotent, donc le laisser en toutes circonstances serait sans danger — mais
  // le J1 commence à l'heure, et huit machines qui installent Docker en parallèle sur une
  // NAT unique, c'est un quart d'heure perdu.
  user_data = var.workstation_ami_id == "" ? file("${path.module}/../../ami/bootstrap.sh") : null

  // Un changement du script ne doit pas remplacer une machine en cours d'utilisation.
  user_data_replace_on_change = false

  // IMDSv2 obligatoire. Sans cela, n'importe quel processus de la machine — y compris un
  // paquet Python compromis — peut lire les credentials du profil d'instance par une
  // simple requête HTTP sans en-tête.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = {
    Name = "qc-${each.key}-workstation"
    Team = each.key
  }
}
