// Répartiteur de charge partagé — tranche 2 du socle.
//
// UN SEUL ALB pour toute la promotion (décision D12). Un ALB par groupe coûterait six
// fois plus cher pour rendre exactement le même service.
//
// Le socle crée l'ALB, son groupe de sécurité, son écouteur :80 — et, depuis la revue du
// 29/07/2026, la règle de routage et le groupe de cibles de CHAQUE binôme (voir
// alb_teams.tf). Le module `team/` n'y touche plus : il accroche seulement son service
// ECS au groupe de cibles précréé. C'est ce qui permet de retirer aux workstations toute
// écriture elasticloadbalancing, donc à un binôme de supprimer la règle d'un autre.
//
//   socle  ──> ALB + écouteur :80 (action par défaut : 404) + règles /gNN/* + TG qc-gNN-tg
//   team   ──> service ECS enregistré dans qc-gNN-tg

resource "aws_security_group" "alb" {
  name        = "qc-promo-alb"
  description = "ALB interne de la promotion"
  vpc_id      = aws_vpc.promo.id

  // ALB interne : le trafic ne vient que du VPC — machines de travail des binômes et,
  // le cas échéant, autres tâches. Aucune exposition à internet.
  ingress {
    description = "HTTP depuis le VPC"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  // L'ALB doit joindre les tâches ECS, qui vivent en sous-réseau privé sur le port 8501.
  egress {
    description = "Vers les taches ECS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "qc-promo-alb"
  }
}

resource "aws_lb" "promo" {
  name               = "qc-promo-alb"
  load_balancer_type = "application"

  // `internal` (décision D12). Les postes de l'entreprise étant verrouillés, les binômes
  // travaillent depuis une machine hébergée dans ce VPC : l'application y est joignable,
  // ce qui suffit au fil rouge. Le mode `internet-facing` reste possible via la variable,
  // mais il exigerait HTTPS, une restriction par CIDR et aucune donnée réelle.
  internal = var.alb_scheme == "internal"

  security_groups = [aws_security_group.alb.id]
  subnets         = [for s in aws_subnet.public : s.id]

  // Un ALB oublié continue d'être facturé. Le socle est détruit en fin de formation, et
  // rien ici ne justifie d'empêcher cette destruction.
  enable_deletion_protection = false

  tags = {
    Name = "qc-promo-alb"
  }
}

// Action par défaut : 404 explicite. Sans elle, un chemin inconnu renverrait une erreur
// technique illisible. Le message nomme la cause probable, parce que c'est exactement ce
// qu'un binôme voit quand il oublie le `/` final ou se trompe de groupe.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.promo.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Groupe inconnu. L'application est servie sur /<TEAM_ID>/ — par exemple /g01/"
    }
  }
}

// --- HTTPS et ACM — écrits, NON appliqués ---------------------------------------------
//
// Décision tranchée du programme : un certificat public ACM ne peut pas être émis pour le
// nom DNS par défaut d'un ALB — AWS ne délègue pas `elb.amazonaws.com`. Il faudrait un
// domaine possédé par l'entreprise, sa validation DNS, et une entrée pointant vers l'ALB.
//
// Le mode dégradé est donc retenu officiellement : l'ALB écoute en HTTP. Tout ce qui suit
// est la configuration qu'exigerait un vrai déploiement, conservée pour la démonstration
// commentée de la masterclass du J2. Elle s'appuie sur du code réel, pas sur un schéma.
//
//   resource "aws_acm_certificate" "promo" {
//     domain_name       = var.public_domain_name        // par ex. labs.exemple.entreprise.fr
//     validation_method = "DNS"
//
//     lifecycle {
//       create_before_destroy = true                    // sinon coupure au renouvellement
//     }
//   }
//
//   resource "aws_lb_listener" "https" {
//     load_balancer_arn = aws_lb.promo.arn
//     port              = 443
//     protocol          = "HTTPS"
//     ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
//     certificate_arn   = aws_acm_certificate.promo.arn
//
//     default_action { ... }
//   }
//
//   // Le :80 ne servirait plus qu'à rediriger, jamais à servir l'application.
//   //   default_action {
//   //     type = "redirect"
//   //     redirect { port = "443", protocol = "HTTPS", status_code = "HTTP_301" }
//   //   }
