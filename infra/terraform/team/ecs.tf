// Définition de tâche et service du binôme.

// Groupe de logs de l'application. Il vit ici et non dans le socle parce que le lab
// Terraform du J3 consiste notamment à changer sa rétention, et que les apprenants n'ont
// la main que sur ce module.
//
// Le créer explicitement fixe la rétention : un groupe créé automatiquement par ECS a une
// rétention infinie, donc une facturation qui ne s'arrête jamais.
resource "aws_cloudwatch_log_group" "app" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name}-app"
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${local.name}-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory

  // Les DEUX rôles, et c'est le point du J2 :
  //
  //   execution_role_arn  l'agent ECS, AVANT le démarrage — tirer l'image, ouvrir les logs
  //   task_role_arn       le code DANS le conteneur — endpoint SageMaker, modèle Bedrock
  //
  // Les confondre donne une tâche qui démarre parfaitement et qui échoue à la première
  // prédiction, avec un AccessDenied dans les logs applicatifs et non dans ceux d'ECS.
  execution_role_arn = local.socle.ecs_execution_role_arn[var.team_id]
  task_role_arn      = local.socle.ecs_task_role_arn[var.team_id]

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${local.socle.ecr_repository_url[var.team_id]}:${var.image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = var.app_port
          protocol      = "tcp"
        },
      ]

      // Aucun credential ici. La tâche prend son identité du rôle applicatif, que la
      // chaîne de résolution boto3 par défaut trouve seule.
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "TEAM_ID", value = var.team_id },
        { name = "OWNER_EMAIL", value = var.owner_email },
        { name = "SAGEMAKER_ENDPOINT_NAME", value = "${local.name}-endpoint" },
        { name = "S3_BUCKET", value = local.socle.s3_bucket_name[var.team_id] },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        // Décision D12 : sans ce préfixe, Streamlit sert ses ressources statiques et son
        // websocket à la racine, que l'ALB ne route pas. Page blanche, tâche saine.
        { name = "STREAMLIT_BASE_URL_PATH", value = local.base_url_path },
        // Origine admise pour le websocket (browser.serverAddress). C'est ce qui permet
        // de garder CORS et la protection XSRF ACTIFS : le navigateur pointe sur l'ALB,
        // Streamlit doit donc reconnaître cette adresse comme la sienne. Sans elle,
        // handshake websocket → 403 et écran de chargement infini (revue du 29/07/2026,
        // point 3).
        { name = "STREAMLIT_BROWSER_SERVER_ADDRESS", value = local.socle.alb_dns_name },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "app"
        }
      }

      // Contrôle de santé du CONTENEUR, distinct de celui du groupe de cibles. Celui-ci
      // interroge l'application depuis l'intérieur de la tâche, donc sans passer par
      // l'ALB : il distingue « l'application est morte » de « l'ALB ne sait pas la
      // joindre ». C'est ce qui permet, au J3, de trancher entre les deux.
      //
      // Il vérifie DEUX choses : que le serveur répond, et que l'application peut lire
      // ses données dans S3. Un contrôle qui ne teste que le port déclare saine une
      // application qui démarre puis s'arrête faute de données — Streamlit sert alors une
      // page d'erreur avec un code 200.
      healthCheck = {
        command = ["CMD-SHELL",
          "curl -f http://localhost:${var.app_port}/${local.base_url_path}/_stcore/health && uv run --frozen python -c 'from qc import inference; inference.local_sample()' || exit 1"
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    },
  ])

  tags = {
    Name = "${local.name}-app"
  }
}

resource "aws_ecs_service" "app" {
  name            = "${local.name}-app"
  cluster         = local.socle.ecs_cluster_name[var.team_id]
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    // Sous-réseaux PRIVÉS. Les tâches sortent par la NAT et ne sont joignables que par
    // l'ALB, dont le groupe de sécurité est le seul autorisé en entrée.
    subnets          = local.socle.private_subnet_ids
    security_groups  = [local.socle.ecs_tasks_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    // Groupe de cibles PRÉCRÉÉ par le socle (revue du 29/07/2026, point 2) : le module
    // team n'écrit plus rien sur l'ALB, il enregistre seulement ses tâches dedans.
    // L'inscription des IP est faite par ECS via son rôle lié au service, pas par
    // l'identité du binôme.
    target_group_arn = local.socle.target_group_arns[var.team_id]
    container_name   = "app"
    container_port   = var.app_port
  }

  // Le temps laissé à l'application pour démarrer avant que l'ALB ne commence à la
  // déclarer malsaine. Streamlit met une trentaine de secondes ; sans ce délai, ECS tue
  // la tâche et la relance en boucle, ce qui ressemble à un plantage applicatif.
  health_check_grace_period_seconds = 90

  // Permet `aws ecs execute-command`, donc d'ouvrir un shell DANS la tâche. C'est l'outil
  // de diagnostic du J3 face à une tâche saine qui ne répond pas.
  enable_execute_command = true

  tags = {
    Name = "${local.name}-app"
  }
}
