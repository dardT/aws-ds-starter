// Passerelle IDE navigateur — code-server sur chaque machine de travail (PLAN.md).
//
// Constat de depart : les binomes n'atteignent leur machine de travail
// (`workstations.tf`, decision D2) que par Session Manager — un terminal nu, sans
// editeur graphique. Le poste de l'entreprise bloque le SSH (meme tunnele par SSM) et
// n'a pas l'AWS CLI : aucun tunnel local n'est possible, seule une URL `http://`
// ordinaire, dans un navigateur non modifie, peut fonctionner.
//
// code-server (VS Code servi en HTTP) tourne SUR la machine existante — meme systeme
// de fichiers que Docker, `uv`, le clone git — et n'a pas d'equivalent au
// `--server.baseUrlPath` de Streamlit (decision D12) : le routage se fait donc par
// PORT, un par binome, plutot que par chemin.
//
// Portee volontairement restreinte a un sandbox de 2 jours sans donnee reelle : pas
// de TLS, pas de Cognito, un mot de passe code-server par equipe sur du HTTP en clair
// — decision explicitement acceptee par le commanditaire, voir PLAN.md section
// « Security posture ».
//
// Tout ce fichier est gate par `create_ide_gateway` (variables.tf), meme idiome de
// garde-fou de cout que `create_mlflow` (mlflow.tf) : tant qu'il vaut false, rien
// n'est cree, et les machines de travail existantes ne sont pas affectees (voir la
// table de securite de PLAN.md).

// --- Groupe de securite de la passerelle ------------------------------------------------

// Bornes du port IDE derivees de `var.teams`, plutot que codees en dur : meme formule
// que l'ecouteur ALB plus bas (10000 + les deux chiffres du TEAM_ID). Si `var.teams`
// grandit au-dela de neuf binomes, ou si les identifiants ne sont pas contigus (un
// groupe retire en cours de route, par exemple), cette plage suit automatiquement,
// sans qu'il faille toucher au security group a la main a chaque ajustement du roster.
locals {
  ide_ports    = [for t in var.teams : 10000 + tonumber(substr(t, 1, 2))]
  ide_port_min = min(local.ide_ports...)
  ide_port_max = max(local.ide_ports...)
}

resource "aws_security_group" "ide_alb" {
  count = var.create_ide_gateway ? 1 : 0

  name        = "qc-promo-ide-alb"
  description = "Passerelle IDE navigateur - premiere exposition internet du compte, ports ${local.ide_port_min}-${local.ide_port_max} uniquement"
  vpc_id      = aws_vpc.promo.id

  // Premiere ingress internet-facing du compte, assumee explicitement (PLAN.md,
  // section Security posture) : sandbox sans donnee reelle, ~2 jours d'exposition.
  ingress {
    description = "code-server, un port par binome (10000 + les deux chiffres du TEAM_ID)"
    from_port   = local.ide_port_min
    to_port     = local.ide_port_max
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Vers les machines de travail du VPC uniquement"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "qc-promo-ide-alb"
  }
}

// Seule modification touchant une ressource existante dans ce plan : `workstations.tf`
// cree `aws_security_group.workstation` volontairement sans la moindre regle
// d'entree (decision D2). Ajouter la regle ICI, comme `aws_security_group_rule`
// separe plutot que comme bloc `ingress` inline dans workstations.tf, evite de
// desynchroniser ce fichier ide.tf-only depuis `create_ide_gateway` : quand la
// variable repasse a false, la regle disparait, la SG de la workstation retrouve
// exactement son etat d'origine, et le commentaire de workstations.tf reste vrai.
//
// Cette SG est PARTAGEE par toutes les machines de travail (une seule
// `aws_security_group.workstation` pour la promotion, pas une par equipe) : activer
// `create_ide_gateway` ouvre donc le port 8080 en ingress pour TOUTES les instances,
// meme quand `ide_gateway_teams` restreint le test a une seule equipe. Sans effet
// pratique pour les equipes non ciblees : aucun `aws_lb_target_group.ide` /
// `aws_lb_listener.ide` ne route vers elles, et aucun code-server n'y est installe
// (aws_ssm_association.code_server_setup, scope lui aussi par ide_gateway_teams) —
// rien n'ecoute derriere ce port sur ces machines.
resource "aws_security_group_rule" "workstation_ide" {
  count = var.create_ide_gateway ? 1 : 0

  type                     = "ingress"
  description              = "code-server, depuis la passerelle IDE uniquement"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  security_group_id        = aws_security_group.workstation.id
  source_security_group_id = aws_security_group.ide_alb[0].id
}

// --- Le repartiteur de charge --------------------------------------------------------

resource "aws_lb" "ide" {
  count = var.create_ide_gateway ? 1 : 0

  name               = "qc-promo-ide-alb"
  load_balancer_type = "application"

  // internet-facing, volontairement (voir commentaire du security group ci-dessus) :
  // c'est la seule facon d'etre joignable depuis un poste sans VPN, sans CLI AWS, sans
  // SSH. Reste isole du reste du socle : aucune modification a alb.tf / alb_teams.tf.
  internal = false

  security_groups = [aws_security_group.ide_alb[0].id]
  subnets         = [for s in aws_subnet.public : s.id]

  // Meme raisonnement que aws_lb.promo (alb.tf) : le socle est detruit en fin de
  // formation, rien ici ne doit bloquer cette destruction.
  enable_deletion_protection = false

  tags = {
    Name = "qc-promo-ide-alb"
  }
}

// --- Par binome : cible, ecoute, mot de passe, distribution ---------------------------
//
// Scope a `ide_gateway_teams` (variables.tf), PAS a `workstation_teams` directement,
// bien que celle-ci reste la valeur par defaut via coalesce(). La raison de cette
// variable separee : les machines de travail de ce socle sont deja VIVANTES pour
// toute la promotion (contrainte dure de PLAN.md). Reduire `workstation_teams` pour
// un test cible reduirait aussi le for_each de `aws_instance.workstation`
// (workstations.tf) et DETRUIRAIT les instances des equipes retirees.
// `ide_gateway_teams = ["g08"]` teste la passerelle sur une seule equipe sans jamais
// toucher a ce for_each — seul le sous-ensemble choisi doit avoir une machine
// existante (voir la validation de la variable), condition necessaire a
// `aws_lb_target_group_attachment.ide` ci-dessous, qui indexe
// `aws_instance.workstation[each.key]` : un `for_each` sur une equipe sans machine
// reelle ferait echouer `terraform apply` (index invalide), pas juste laisser une
// ressource orpheline.

locals {
  ide_teams = var.create_ide_gateway ? toset(coalesce(var.ide_gateway_teams, var.workstation_teams, var.teams)) : toset([])
}

resource "aws_lb_target_group" "ide" {
  for_each = local.ide_teams

  name        = "qc-${each.key}-ide-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.promo.id
  target_type = "instance" // cible EC2, pas IP : contrairement au TG Streamlit (alb_teams.tf), pas d'auto-enregistrement Fargate

  health_check {
    // Chemin documente de code-server. A CONFIRMER a la main avant d'elargir au-dela
    // de la repetition generale a une equipe (PLAN.md, Rollout order, etape 0) :
    // `curl -i localhost:8080/healthz` sans authentification doit repondre 200, sinon
    // toutes les cibles resteraient marquees malsaines en permanence.
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30 // meme valeur que le TG Streamlit (alb_teams.tf)

  tags = {
    Name = "qc-${each.key}-ide-tg"
    Team = each.key
  }
}

// Cible EC2 explicite : contrairement au TG Streamlit (`target_type = "ip"`, qui
// s'auto-enregistre via le role lie au service ECS), une instance EC2 doit etre
// attachee a la main.
resource "aws_lb_target_group_attachment" "ide" {
  for_each = aws_lb_target_group.ide

  target_group_arn = each.value.arn
  target_id        = aws_instance.workstation[each.key].id
  port             = 8080
}

// Pas de regle de chemin : un port = une equipe = une action par defaut, contrairement
// a l'ecouteur :80 partage de alb.tf qui route par prefixe de chemin.
resource "aws_lb_listener" "ide" {
  for_each = aws_lb_target_group.ide

  load_balancer_arn = aws_lb.ide[0].arn
  // g01 -> 10001, g02 -> 10002, ... g09 -> 10009 — meme formule deterministe que la
  // priorite de regle ALB dans alb_teams.tf, pour ne stocker nulle part une table
  // TEAM_ID -> port redondante.
  port     = 10000 + tonumber(substr(each.key, 1, 2))
  protocol = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = each.value.arn
  }

  tags = {
    Name = "qc-${each.key}-ide-listener"
    Team = each.key
  }
}

// 20 caracteres alphanumeriques : pas de caractere qui demanderait un echappement
// shell une fois colle dans le formulaire de connexion de code-server.
resource "random_password" "code_server" {
  for_each = local.ide_teams

  length  = 20
  special = false
}

// Distribue a la machine par le script SSM ci-dessous. SecureString : la machine le
// lit avec son role d'instance existant (ssm:GetParameter, ajoute dans la politique
// `data.aws_iam_policy_document.workstation` de workstations.tf) ; aucune cle
// kms:Decrypt supplementaire n'est necessaire, la cle geree `alias/aws/ssm` par
// defaut autorise deja tout appelant du compte qui detient ssm:GetParameter sur la
// ressource.
resource "aws_ssm_parameter" "code_server_password" {
  for_each = random_password.code_server

  name  = "/qc/${each.key}/code-server-password"
  type  = "SecureString"
  value = each.value.result

  tags = {
    Team = each.key
  }
}

// --- Livraison sur la machine deja active : Run Command, jamais user_data -------------
//
// `aws_instance.workstation` (workstations.tf) n'est touche NULLE PART par ce fichier.
// Modifier `user_data` sur une instance EN COURS force le provider AWS a l'arreter, la
// modifier, puis la redemarrer — meme avec `user_data_replace_on_change = false`, qui
// ne controle que replace-vs-update-en-place, pas ce cycle stop/start propre a la
// mise a jour en place. Sur des machines qui ont deja des terminaux ouverts, des
// editeurs ouverts ou des constructions en cours, cette coupure est precisement ce
// que ce plan exclut (voir PLAN.md, "Hard constraint").
//
// `aws_ssm_association` passe par le meme canal que l'agent SSM deja utilise pour
// Session Manager (decision D2) : ni redemarrage, ni arret, aucun contact avec le
// volume EBS ou quoi que ce soit d'autre deja en cours d'execution sur la machine.
resource "aws_ssm_association" "code_server_setup" {
  for_each = local.ide_teams

  name = "AWS-RunShellScript"

  targets {
    key    = "tag:Team"
    values = [each.key]
  }

  parameters = {
    // Rendu PAR EQUIPE ici, a l'apply : la machine n'a jamais besoin de se
    // demander a qui elle appartient, la reponse est deja ecrite dans le script
    // qu'elle recoit.
    commands = templatefile("${path.module}/ide_setup.sh.tftpl", {
      team_id = each.key
      region  = var.region
    })
  }

  // Le script lit lui-meme le mot de passe via `aws ssm get-parameter` — Terraform
  // n'a donc AUCUN lien implicite avec `aws_ssm_parameter.code_server_password`
  // (sa valeur n'apparait jamais dans le template). Sans ce depends_on explicite, le
  // script pourrait s'executer avant que le parametre n'existe.
  depends_on = [aws_ssm_parameter.code_server_password]
}
