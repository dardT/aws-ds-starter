// Passerelle IDE navigateur — code-server sur chaque machine de travail (PLAN.md).
//
// Constat de depart : les binomes n'atteignent leur machine de travail
// (`workstations.tf`, decision D2) que par Session Manager — un terminal nu, sans
// editeur graphique. Le poste de l'entreprise bloque le SSH (meme tunnele par SSM) et
// n'a pas l'AWS CLI : aucun tunnel local n'est possible, seule une URL ordinaire, dans
// un navigateur non modifie, peut fonctionner.
//
// code-server (VS Code servi en HTTP) tourne SUR la machine existante — meme systeme
// de fichiers que Docker, `uv`, le clone git.
//
// ROUTAGE PAR CHEMIN, depuis le 15/09/2026 (decision D28). La premiere version de ce
// fichier routait par PORT (un ecouteur par binome, 10000 + les deux chiffres du
// TEAM_ID) faute de domaine et parce que code-server n'a pas d'equivalent au
// `--server.baseUrlPath` de Streamlit (decision D12). Le formateur possede desormais
// `vsc0de.fr` et a cree le sous-domaine `ide.vsc0de.fr` (CNAME chez Hostinger vers cet ALB,
// decision D29 — un sous-domaine plutot que l'apex, pour qu'un simple CNAME suffise) :
// la passerelle est servie en HTTPS sur ce sous-domaine, chemin litteral par binome —
// `https://ide.vsc0de.fr/g01/`, `/g02/`, …
//
// Pourquoi un nginx sur la machine, et pas juste une regle ALB. Une regle
// `path_pattern` transmet le chemin TEL QUEL a la cible : l'ALB ne sait pas retirer un
// prefixe, contrairement au `proxy_pass http://backend/;` de nginx (la barre oblique
// finale). code-server recevrait donc `/g01/…` et chercherait ses ressources statiques
// au mauvais endroit — page blanche. Un nginx minimal, pousse sur chaque machine par le
// meme canal SSM que code-server (`ide_setup.sh.tftpl`), ecoute en :8081, retire le
// prefixe `/gNN/` et relaie vers le code-server local en :8080. Le :8080 ne quitte
// jamais la boucle locale.
//
// Portee volontairement restreinte a un sandbox de 2 jours sans donnee reelle : pas de
// Cognito, un mot de passe code-server par equipe — decision explicitement acceptee par
// le commanditaire, voir PLAN.md section « Security posture ». Le TLS, lui, n'est plus
// une exception : il vient gratuitement avec ACM des lors qu'un domaine existe.
//
// Tout ce fichier est gate par `create_ide_gateway` (variables.tf), meme idiome de
// garde-fou de cout que `create_mlflow` (mlflow.tf) : tant qu'il vaut false, rien
// n'est cree, et les machines de travail existantes ne sont pas affectees (voir la
// table de securite de PLAN.md).

// --- Groupe de securite de la passerelle ------------------------------------------------

resource "aws_security_group" "ide_alb" {
  count = var.create_ide_gateway ? 1 : 0

  // `name_prefix`, pas `name` : `description` ci-dessous est immuable cote AWS (toute
  // modification force un remplacement de la SG), et l'ALB (aws_lb.ide) reste attache a
  // l'ancienne le temps de la bascule. Sans `create_before_destroy`, Terraform detruirait
  // l'ancienne AVANT que l'ALB ne soit repointe dessus — DependencyViolation garantie
  // (ENI de l'ALB toujours attachee). Avec `create_before_destroy`, la nouvelle SG doit
  // exister en parallele de l'ancienne un court instant : un `name` fixe entrerait en
  // collision (noms de SG uniques par VPC), d'ou `name_prefix`. Le nom reel devient donc
  // `qc-promo-ide-alb-xxxxxxxx` (voir PLAN_DNS.md, verification par prefixe).
  name_prefix = "qc-promo-ide-alb-"
  description = "Passerelle IDE navigateur - premiere exposition internet du compte, 443 (et 80 en simple redirection)"
  vpc_id      = aws_vpc.promo.id

  // Premiere ingress internet-facing du compte, assumee explicitement (PLAN.md,
  // section Security posture) : sandbox sans donnee reelle, ~2 jours d'exposition.
  // Deux ports seulement depuis le routage par chemin (D28), au lieu de la plage
  // 10001-10009 d'avant.
  ingress {
    // AWS restreint les caracteres d'une description de regle : pas de < ni >, donc
    // pas de /<TEAM_ID>/ ici comme ailleurs dans ce fichier.
    description = "HTTPS, code-server derriere nginx - un chemin /gNN/ par binome"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  // Le :80 ne sert JAMAIS de contenu : son unique action est une redirection 301 vers
  // le :443 (voir `aws_lb_listener.ide_http` plus bas). Il existe parce qu'un binome
  // qui tape `ide.vsc0de.fr/g01/` sans schema atterrit en http:// — sans cet ecouteur, la
  // connexion serait refusee et l'erreur du navigateur n'indiquerait rien d'utile.
  ingress {
    description = "HTTP, redirection 301 vers 443 uniquement - aucun contenu servi ici"
    from_port   = 80
    to_port     = 80
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

  lifecycle {
    create_before_destroy = true
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
// `create_ide_gateway` ouvre donc le port 8081 en ingress pour TOUTES les instances,
// meme quand `ide_gateway_teams` restreint le test a une seule equipe. Sans effet
// pratique pour les equipes non ciblees : aucun `aws_lb_target_group.ide` /
// `aws_lb_listener_rule.ide` ne route vers elles, et aucun nginx ni code-server n'y est
// installe (aws_ssm_association.code_server_setup, scope lui aussi par
// ide_gateway_teams) — rien n'ecoute derriere ce port sur ces machines.
//
// 8081 et non 8080 depuis le routage par chemin (D28) : c'est nginx qui est expose,
// lui seul sait retirer le prefixe `/gNN/`. Le code-server lui-meme reste en :8080 et
// ne traverse JAMAIS cette SG — aucune regle ne l'y autorise, il n'est joignable que
// par la boucle locale de sa propre machine, depuis nginx.
resource "aws_security_group_rule" "workstation_ide" {
  count = var.create_ide_gateway ? 1 : 0

  type                     = "ingress"
  description              = "nginx (relais vers code-server local), depuis la passerelle IDE uniquement"
  from_port                = 8081
  to_port                  = 8081
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

// --- Certificat ACM — validation DNS chez un registrar EXTERNE -------------------------
//
// `vsc0de.fr` est enregistre chez Hostinger, PAS delegue a Route 53 : il n'y a donc
// aucune `aws_route53_zone` ni `aws_route53_record` dans ce socle, et il ne faut pas en
// ajouter — la zone n'appartient pas a ce compte AWS.
//
// Le couple certificat + validation fonctionne quand meme : ACM publie les CNAME
// qu'il attend dans `domain_validation_options` (sortis par
// `terraform output ide_certificate_validation_records`), le formateur les cree a la
// main dans le panneau DNS de Hostinger (hPanel), et `aws_acm_certificate_validation`
// ne fait qu'interroger ACM en boucle jusqu'a ce que le certificat passe ISSUED.
//
// CONSEQUENCE ATTENDUE, PAS UN BUG : au premier `terraform apply`, l'execution se FIGE
// sur `aws_acm_certificate_validation.ide` — jusqu'a 45 minutes — tant que le CNAME
// n'existe pas chez Hostinger. Marche a suivre : lancer un premier apply, le laisser
// bloquer (ou l'interrompre), lire `terraform output ide_certificate_validation_records`,
// creer l'enregistrement chez Hostinger, relancer l'apply. Les applys suivants ne
// bloquent plus : le certificat reste valide.
resource "aws_acm_certificate" "ide" {
  count = var.create_ide_gateway ? 1 : 0

  domain_name       = var.ide_domain_name
  validation_method = "DNS"

  lifecycle {
    // Sans cela, le renouvellement detruirait le certificat avant d'attacher le
    // nouveau — l'ecouteur HTTPS se retrouverait sans `certificate_arn` valide, donc
    // la passerelle coupee, le temps d'un apply.
    create_before_destroy = true
  }

  tags = {
    Name = "qc-promo-ide-cert"
  }
}

resource "aws_acm_certificate_validation" "ide" {
  count = var.create_ide_gateway ? 1 : 0

  certificate_arn = aws_acm_certificate.ide[0].arn

  // Avec Route 53 on passerait ici les FQDN des `aws_route53_record` crees par
  // Terraform. Le DNS etant externe, on renvoie simplement les noms que ACM a lui-meme
  // demandes : la ressource n'a besoin que de savoir QUOI attendre, pas de savoir qui a
  // cree l'enregistrement.
  validation_record_fqdns = [for o in aws_acm_certificate.ide[0].domain_validation_options : o.resource_record_name]
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
  port        = 8081 // nginx, pas code-server : c'est lui qui retire le prefixe /<TEAM_ID>/ (D28)
  protocol    = "HTTP"
  vpc_id      = aws_vpc.promo.id
  target_type = "instance" // cible EC2, pas IP : contrairement au TG Streamlit (alb_teams.tf), pas d'auto-enregistrement Fargate

  health_check {
    // Chemin documente de code-server, PREFIXE par le TEAM_ID puisque la sonde passe
    // desormais par nginx, exactement comme le /healthz Streamlit de alb_teams.tf.
    // Viser `/healthz` sans prefixe atteindrait le `server` nginx sans `location`
    // correspondante, donc un 404 : l'ALB declarerait la cible malsaine, la retirerait,
    // et l'IDE deviendrait injoignable alors que code-server tourne parfaitement.
    // A CONFIRMER a la main avant d'elargir au-dela de la repetition generale a une
    // equipe (PLAN.md, Rollout order, etape 0) : sur la machine, un
    // `curl -i localhost:8081/gNN/healthz` sans authentification doit repondre 200.
    path                = "/${each.key}/healthz"
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
  port             = 8081
}

// --- Ecouteurs : UN SEUL :443 partage, plus un :80 de simple redirection --------------
//
// Avant D28, ce fichier creait un ecouteur PAR EQUIPE, un port chacun. Le domaine rend
// le routage par chemin possible, donc la meme forme que l'ALB Streamlit de alb.tf :
// un ecouteur partage, une action par defaut en 404 explicite, et une regle de chemin
// par binome (plus bas).
resource "aws_lb_listener" "ide" {
  count = var.create_ide_gateway ? 1 : 0

  load_balancer_arn = aws_lb.ide[0].arn
  port              = 443
  protocol          = "HTTPS"

  // Meme politique que l'exemple HTTPS (ecrit, non applique) de alb.tf.
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  // Reference la RESSOURCE DE VALIDATION, pas le certificat : c'est ce qui force
  // Terraform a n'attacher le certificat qu'une fois ACM passe ISSUED. Referencer
  // `aws_acm_certificate.ide[0].arn` directement creerait l'ecouteur avec un certificat
  // encore PENDING_VALIDATION, qui ne servirait rien.
  certificate_arn = aws_acm_certificate_validation.ide[0].certificate_arn

  // 404 explicite, meme raisonnement que `aws_lb_listener.http` (alb.tf) : sans elle,
  // un chemin inconnu renverrait une erreur technique illisible. Le message nomme la
  // cause probable, parce que c'est exactement ce qu'un binome voit quand il se trompe
  // de groupe ou tape la racine du domaine.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Groupe inconnu. L'application est servie sur /<TEAM_ID>/ — par exemple /g01/"
    }
  }

  tags = {
    Name = "qc-promo-ide-listener"
  }
}

// Le :80 ne sert JAMAIS l'IDE : il redirige, point. Meme forme que le bloc de
// redirection ecrit (mais commente, jamais applique) dans alb.tf. Sans lui, un binome
// qui tape `ide.vsc0de.fr/g01/` sans schema — ce que fait tout navigateur par defaut —
// obtiendrait une connexion refusee au lieu d'etre pousse vers le HTTPS.
resource "aws_lb_listener" "ide_http" {
  count = var.create_ide_gateway ? 1 : 0

  load_balancer_arn = aws_lb.ide[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    // Le chemin et la requete sont conserves par defaut (#{path}, #{query}) : la
    // redirection depuis /g01/ arrive bien sur https://.../g01/, pas sur la racine.
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = {
    Name = "qc-promo-ide-listener-http"
  }
}

// Une regle de chemin par binome, greffee sur l'ecouteur :443 partage — copie conforme
// de `aws_lb_listener_rule.team` (alb_teams.tf), a la cible pres.
resource "aws_lb_listener_rule" "ide" {
  for_each = local.ide_teams

  listener_arn = aws_lb_listener.ide[0].arn

  // Priorite unique par groupe, derivee du TEAM_ID : g01 -> 10, g02 -> 20. Meme formule
  // que alb_teams.tf, volontairement : deux regles de meme priorite sur un ecouteur
  // produisent une erreur a l'apply, et il n'y a aucune raison d'avoir deux conventions
  // de priorite dans le meme socle. Les deux ecouteurs etant distincts, il n'y a aucune
  // collision avec les regles Streamlit.
  priority = tonumber(substr(each.key, 1, 2)) * 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ide[each.key].arn
  }

  condition {
    path_pattern {
      // Les DEUX motifs, meme piege que dans alb_teams.tf : sans `/g01`, un binome qui
      // tape l'adresse sans barre oblique finale tombe sur le 404 par defaut de
      // l'ecouteur — et croit que son IDE n'est pas installe.
      values = ["/${each.key}", "/${each.key}/*"]
    }
  }

  tags = {
    Name = "qc-${each.key}-ide-rule"
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
      team_id     = each.key
      region      = var.region
      domain_name = var.ide_domain_name
    })
  }

  // Le script lit lui-meme le mot de passe via `aws ssm get-parameter` — Terraform
  // n'a donc AUCUN lien implicite avec `aws_ssm_parameter.code_server_password`
  // (sa valeur n'apparait jamais dans le template). Sans ce depends_on explicite, le
  // script pourrait s'executer avant que le parametre n'existe.
  depends_on = [aws_ssm_parameter.code_server_password]
}
