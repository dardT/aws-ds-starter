// Calcul conteneurisé — tranche 2 du socle.
//
// Le socle crée les clusters et le groupe de sécurité des tâches. Il ne crée NI
// définition de tâche NI service : ceux-là naissent au J2, quand le binôme a construit
// son image. C'est la même règle que pour SageMaker au J1 — Terraform pose le terrain,
// l'apprenant crée ce qui porte la notion enseignée.
//
// Un cluster PAR GROUPE, et non un cluster partagé. Le programme se contredisait sur ce
// point : il nomme `qc-<TEAM_ID>-cluster` dans la règle de nommage, et parle d'un cluster
// unique dans le paragraphe sur la séparation des états. Un cluster ECS ne coûte rien —
// c'est un simple regroupement logique — et la règle de nommage par préfixe prime, parce
// qu'elle porte l'isolation et les conditions IAM. Les clusters restent créés ici, dans le
// socle, pour que le `terraform destroy` du J3 ne puisse pas les emporter.

resource "aws_ecs_cluster" "team" {
  for_each = toset(var.teams)

  name = "qc-${each.key}-cluster"

  setting {
    // Container Insights publie RunningTaskCount, CpuUtilized et MemoryUtilized dans
    // CloudWatch. L'alarme « plus aucune tâche en service » du J3 en dépend : sans
    // Insights, cette métrique n'existe pas et l'alarme reste éternellement en
    // INSUFFICIENT_DATA.
    //
    // Facturé à la métrique publiée. Sur six clusters d'un service chacun, sur quelques
    // jours, l'ordre de grandeur est de quelques euros.
    //
    // À ne pas confondre avec `enable_execute_command`, qui ouvre un shell dans la tâche
    // et se déclare sur le SERVICE, dans le module team.
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Team = each.key
  }
}

resource "aws_ecs_cluster_capacity_providers" "team" {
  for_each = aws_ecs_cluster.team

  cluster_name       = each.value.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  // Par défaut, les tâches partent sur FARGATE, pas sur FARGATE_SPOT : une tâche Spot
  // peut être interrompue avec deux minutes de préavis, ce qui ferait disparaître
  // l'application d'un binôme au milieu d'une démonstration. FARGATE_SPOT reste déclaré
  // pour que le J3 puisse en parler, et l'essayer, en connaissance de cause.
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

// Groupe de sécurité des tâches, partagé par tous les groupes.
//
// Les tâches n'acceptent de trafic QUE depuis l'ALB, et uniquement sur le port de
// l'application. Une tâche joignable depuis n'importe où dans le VPC contournerait le
// routage par chemin, et un binôme pourrait atteindre l'application d'un autre en visant
// directement son adresse privée.
resource "aws_security_group" "ecs_tasks" {
  name        = "qc-promo-ecs-tasks"
  description = "Taches ECS - entree depuis le seul ALB (ASCII impose par AWS)"
  vpc_id      = aws_vpc.promo.id

  ingress {
    description     = "Application, depuis l ALB uniquement"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  // Sortie ouverte : la tâche doit joindre l'endpoint SageMaker, Bedrock, ECR et
  // CloudWatch. Avec `enable_vpc_endpoints = false` (décision D9), tout cela passe par la
  // passerelle NAT.
  egress {
    description = "Vers les services AWS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "qc-promo-ecs-tasks"
  }
}
