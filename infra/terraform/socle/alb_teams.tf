// Routage par binôme sur l'ALB partagé — précréé par le SOCLE.
//
// Revue du 29/07/2026, point 2. Ces deux objets vivaient dans le module `team/`, ce qui
// obligeait à accorder aux workstations les écritures elasticloadbalancing sur `*` :
// CreateRule et DeleteRule ne portent que sur l'ARN de l'écouteur, commun à toute la
// promotion, et un binôme pouvait donc supprimer la règle d'un autre.
//
// Précréer une règle et un groupe de cibles par binôme ICI supprime le besoin : le rôle
// workstation n'a plus AUCUNE écriture elasticloadbalancing. Le module `team/` se
// contente d'accrocher son service ECS au groupe de cibles, dont il lit l'ARN dans les
// sorties du socle.
//
//   socle  ──> ALB + écouteur :80 + règle /gNN/* ──> groupe de cibles qc-gNN-tg (vide)
//   team   ──> service ECS enregistré dans qc-gNN-tg

resource "aws_lb_target_group" "team" {
  for_each = toset(var.teams)

  name     = "qc-${each.key}-tg"
  port     = var.app_port
  protocol = "HTTP"
  vpc_id   = aws_vpc.promo.id

  // Fargate utilise le mode réseau awsvpc : chaque tâche a sa propre adresse IP dans le
  // sous-réseau. La cible est donc l'IP, pas une instance EC2.
  target_type = "ip"

  health_check {
    // Chemin natif de Streamlit, PRÉFIXÉ par le TEAM_ID puisque l'application est servie
    // sous ce préfixe. Viser /_stcore/health sans préfixe renvoie 404 : l'ALB déclare la
    // cible malsaine, la retire, et l'application devient injoignable alors que la tâche
    // tourne parfaitement.
    path                = "/${each.key}/_stcore/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  // Le temps laissé à l'ALB pour vider les connexions avant de retirer une cible. 30 s
  // au lieu des 300 s par défaut : au J3, un changement de `desired_count` ne doit pas
  // prendre cinq minutes à se voir.
  deregistration_delay = 30

  tags = {
    Name = "qc-${each.key}-tg"
    Team = each.key
  }
}

resource "aws_lb_listener_rule" "team" {
  for_each = toset(var.teams)

  listener_arn = aws_lb_listener.http.arn

  // Priorité unique par groupe, dérivée du TEAM_ID : g01 → 10, g02 → 20. Deux règles de
  // même priorité sur un écouteur produisent une erreur à l'apply.
  priority = tonumber(substr(each.key, 1, 2)) * 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.team[each.key].arn
  }

  condition {
    path_pattern {
      // Les DEUX motifs. Sans `/g01`, un utilisateur qui tape l'adresse sans barre
      // oblique finale tombe sur le 404 par défaut de l'écouteur — et croit que son
      // application n'est pas déployée.
      values = ["/${each.key}", "/${each.key}/*"]
    }
  }

  tags = {
    Name = "qc-${each.key}-rule"
    Team = each.key
  }
}
