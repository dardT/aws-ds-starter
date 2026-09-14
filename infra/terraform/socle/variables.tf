variable "region" {
  description = "Région AWS. Source de vérité unique, identique à AWS_REGION du .env (§12)."
  type        = string
  default     = "eu-west-3"
}

variable "project" {
  description = "Valeur du tag Project, imposée par §11."
  type        = string
  default     = "aws-ds"
}

variable "owner_email" {
  description = "Valeur du tag Owner. Adresse du formateur."
  type        = string
}

// Décision D1 : le nombre de groupes n'est pas figé dans le code. Toutes les ressources
// par groupe sont générées par for_each sur cette liste. Ajouter un binôme la veille de
// la formation = ajouter une entrée ici.
variable "teams" {
  description = "Identifiants des binômes. Format imposé : g01, g02, ... (regex ^g[0-9]{2}$)."
  type        = list(string)
  default     = ["g01", "g02", "g03", "g04", "g05", "g06", "g07", "g08", "g09"]

  validation {
    condition     = alltrue([for t in var.teams : can(regex("^g[0-9]{2}$", t))])
    error_message = "Chaque TEAM_ID doit valoir g suivi de deux chiffres — g01, g02, …"
  }

  validation {
    condition     = length(distinct(var.teams)) == length(var.teams)
    error_message = "Les TEAM_ID doivent être uniques : le préfixe porte toute l'isolation entre groupes."
  }
}

variable "log_retention_days" {
  description = <<-EOT
    Rétention des groupes de logs. §14 retient 7 jours.
    C'est l'une des variables que les binômes modifient au J3 dans le module team.
  EOT
  type        = number
  default     = 7
}

variable "data_retention_days" {
  description = "Durée avant suppression automatique des objets S3 des labs (§14)."
  type        = number
  default     = 30
}

variable "bedrock_model_id" {
  description = <<-EOT
    Modèle Bedrock autorisé pour les groupes. Sert à borner la politique IAM au seul
    modèle utilisé, plutôt que d'ouvrir bedrock:InvokeModel sur "*".

    Décision D14 : c'est le seul modèle réellement activé dans le compte. Peut être un
    modelId brut ou un profil d'inférence eu.* / global.* — les deux formes coexistent
    en Europe (décision D6).
  EOT
  type        = string
  default     = "mistral.mistral-large-2402-v1:0"
}

// =====================================================================================
// Tranche 2 — réseau, exposition, machines de travail
// =====================================================================================

variable "vpc_cidr" {
  description = "Plage du VPC de la promotion. Les sous-réseaux en sont dérivés."
  type        = string
  default     = "10.0.0.0/16"
}

variable "enable_vpc_endpoints" {
  description = <<-EOT
    Endpoints d'interface pour ECR (api + dkr), SageMaker (api + runtime), Bedrock
    runtime, CloudWatch Logs, STS et SSM (ssm + ssmmessages + ec2messages). L'endpoint
    S3 gateway, gratuit, est créé inconditionnellement.

    Décision D9 amendée le 30/07/2026 (revue technique, point 7) : `true` par défaut.
    L'environnement cible ne garantit pas la sortie internet, et les endpoints ne la
    suppriment pas — c'est le retrait de la NAT qui la supprime. NAT et endpoints
    coexistent : trafic AWS par les endpoints, le reste par la NAT. Le parcours sans
    NAT du tout est documenté dans infra/ami/README.md.
  EOT
  type        = bool
  default     = true
}

variable "alb_scheme" {
  description = <<-EOT
    `internal` (retenu, décision D12) ou `internet-facing`.

    Les postes de l'entreprise étant verrouillés, les binômes travaillent depuis une machine du
    VPC : l'ALB interne suffit. Le mode `internet-facing` exigerait HTTPS, une restriction
    par CIDR et aucune donnée réelle.
  EOT
  type        = string
  default     = "internal"

  validation {
    condition     = contains(["internal", "internet-facing"], var.alb_scheme)
    error_message = "alb_scheme doit valoir internal ou internet-facing."
  }
}

variable "app_port" {
  description = "Port du conteneur Streamlit. 8501 est la valeur par défaut de Streamlit."
  type        = number
  default     = 8501
}

variable "create_workstations" {
  description = <<-EOT
    Crée les machines de travail EC2. `false` par défaut, et c'est un garde-fou de coût :
    le socle s'applique des semaines à l'avance, les machines ne s'allument que la veille.

    Passer à `true`, appliquer, puis lancer `make workstation-check`.
  EOT
  type        = bool
  default     = false
}

variable "workstation_teams" {
  description = <<-EOT
    Sous-ensemble des équipes qui reçoivent une machine de travail quand
    `create_workstations = true`. `null` (défaut) = toutes les équipes de `teams`.

    Sert à allumer une seule machine — répétition générale, démo, équipe arrivée en
    retard — sans payer les six : `workstation_teams = ["g01"]`.
  EOT
  type        = list(string)
  default     = null

  validation {
    condition = var.workstation_teams == null || alltrue([
      for t in coalesce(var.workstation_teams, []) : contains(var.teams, t)
    ])
    error_message = "Chaque entrée de workstation_teams doit exister dans teams."
  }
}

variable "workstation_ami_id" {
  description = <<-EOT
    AMI dorée des machines de travail (décision D2) : Docker et buildx, uv, git, dépôt
    starter précloné. Vide = dernier Ubuntu 24.04 officiel, à provisionner à la main.
  EOT
  type        = string
  default     = ""
}

variable "workstation_instance_type" {
  description = "Type des machines de travail. t3.large : 2 vCPU et 8 Go, suffisant pour construire une image Docker."
  type        = string
  default     = "t3.large"
}

variable "workstation_disk_gb" {
  description = "Disque des machines de travail. Les images Docker et les couches de construction occupent vite plusieurs gigaoctets."
  type        = number
  default     = 50
}

variable "create_ide_gateway" {
  description = <<-EOT
    Crée la passerelle IDE navigateur (code-server) pour les machines de travail — un
    second ALB internet-facing, ports 10001-10009, un par binôme (voir `ide.tf` et
    PLAN.md). `false` par défaut, même garde-fou de coût que `create_mlflow` /
    `create_workstations` : la première exposition internet du compte ne doit pas
    exister avant qu'on en ait explicitement besoin.

    Nécessite `create_workstations = true` — la passerelle attache le port 8080 des
    machines de travail déjà créées, elle n'a aucun sens sans elles.
  EOT
  type        = bool
  default     = false

  validation {
    condition     = !var.create_ide_gateway || var.create_workstations
    error_message = "create_ide_gateway nécessite create_workstations = true."
  }
}

variable "ide_gateway_teams" {
  description = <<-EOT
    Sous-ensemble d'équipes qui reçoivent la passerelle IDE navigateur quand
    `create_ide_gateway = true`. `null` (défaut) = le même sous-ensemble que
    `workstation_teams` (donc `teams` en entier si celle-ci vaut aussi `null`).

    Délibérément DÉCORRÉLÉE de `workstation_teams` : dans ce socle, les machines de
    travail sont déjà VIVANTES pour toute la promotion (contrainte dure de PLAN.md —
    rien ne doit stopper/remplacer une instance en cours d'usage). Réduire
    `workstation_teams` réduirait aussi le for_each de `aws_instance.workstation`
    (workstations.tf) et DÉTRUIRAIT les instances des équipes retirées de la liste.

    `ide_gateway_teams` permet de tester la passerelle sur une seule équipe (ou une
    poignée), par exemple `ide_gateway_teams = ["g08"]`, sans toucher au for_each des
    machines existantes — seules les ressources ide.tf de cette équipe sont créées.
    `make ide-credentials` / `make ide-check` acceptent la même restriction via la
    variable d'environnement IDE_TEAMS (ex. `IDE_TEAMS=g08 make ide-check`).
  EOT
  type        = list(string)
  default     = null

  validation {
    // Chaque équipe visée doit avoir une machine de travail RÉELLE :
    // aws_lb_target_group_attachment.ide (ide.tf) indexe aws_instance.workstation par
    // TEAM_ID, et un for_each sur une équipe sans instance ferait échouer l'apply
    // (index invalide), pas juste laisser une ressource orpheline — même piège que
    // celui documenté pour workstation_teams plus haut.
    condition = var.ide_gateway_teams == null || alltrue([
      for t in coalesce(var.ide_gateway_teams, []) : contains(coalesce(var.workstation_teams, var.teams), t)
    ])
    error_message = "Chaque entrée de ide_gateway_teams doit avoir une machine de travail existante (voir workstation_teams)."
  }
}

// =====================================================================================
// Tranche 3 — MLflow et alarmes
// =====================================================================================

variable "create_mlflow" {
  description = <<-EOT
    Crée le serveur de suivi MLflow managé. `false` par défaut : il est facturé à l'heure
    dès sa création, qu'on l'utilise ou non.

    À passer à `true` la veille du J3, pas avant. Compter une vingtaine de minutes de
    création — c'est la ressource la plus lente du socle.

    Disponibilité vérifiée en eu-west-3 le 28/07/2026, ce qui lève le risque de D8.
  EOT
  type        = bool
  default     = false
}

variable "mlflow_server_size" {
  description = "Taille du serveur MLflow. Small suffit : le J3 enregistre quelques dizaines d'exécutions."
  type        = string
  default     = "Small"

  validation {
    condition     = contains(["Small", "Medium", "Large"], var.mlflow_server_size)
    error_message = "mlflow_server_size doit valoir Small, Medium ou Large."
  }
}

variable "alarm_treat_missing_data" {
  description = <<-EOT
    Comportement de l'alarme « service sans tâche » quand la métrique cesse d'arriver.

    `breaching` (défaut) : l'absence de données déclenche l'ALARM. C'est la seule valeur
    qui détecte un service détruit — un service supprimé n'émet plus rien, et avec
    `missing` l'alarme resterait en INSUFFICIENT_DATA pour toujours.

    COMPROMIS FORMATION : avec `breaching`, l'extinction du soir (make destroy) déclenche
    une fausse alarme par groupe. Passer à `missing` pendant la formation si ce bruit
    gêne, en acceptant de ne plus détecter les services arrêtés.
  EOT
  type        = string
  default     = "breaching"

  validation {
    condition     = contains(["breaching", "missing"], var.alarm_treat_missing_data)
    error_message = "alarm_treat_missing_data doit valoir breaching ou missing."
  }
}

variable "latence_max_microsecondes" {
  description = <<-EOT
    Seuil de l'alarme de latence, en MICROsecondes. 1 000 000 = 1 seconde.

    L'unité est la source d'erreur classique : un seuil écrit en millisecondes déclenche
    l'alarme en permanence, et on croit à un problème de modèle.
  EOT
  type        = number
  default     = 1000000
}
