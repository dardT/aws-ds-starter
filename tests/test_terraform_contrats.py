"""Contrats sur le HCL du socle — lus comme du texte, sans dépendance Terraform.

Ces tests ne valident pas la syntaxe (c'est le rôle de `terraform validate`, cible
`make ci`) : ils épinglent des DÉCISIONS que le code doit continuer de porter.
Chaque assertion cassée signale qu'une décision de la revue technique du 29/07/2026
a été défaite, pas une erreur de frappe.
"""

from __future__ import annotations

import re
from pathlib import Path

TERRAFORM = Path(__file__).resolve().parent.parent / "infra" / "terraform"
SOCLE = TERRAFORM / "socle"
TEAM = TERRAFORM / "team"


def _bloc(texte: str, entete: str) -> str:
    """Extrait un bloc HCL de premier niveau (de son en-tête à l'accolade fermante)."""
    debut = texte.index(entete)
    profondeur = 0
    for i, caractere in enumerate(texte[debut:], start=debut):
        if caractere == "{":
            profondeur += 1
        elif caractere == "}":
            profondeur -= 1
            if profondeur == 0:
                return texte[debut : i + 1]
    raise AssertionError(f"bloc non refermé : {entete}")


class TestAlarmeServiceSansTache:
    """Revue 29/07, point 1 : `missing` rendait l'alarme aveugle au service détruit."""

    def test_l_absence_de_donnees_declenche_l_alarme(self):
        alarme = _bloc(
            (SOCLE / "alarms.tf").read_text(encoding="utf-8"),
            'resource "aws_cloudwatch_metric_alarm" "service_sans_tache"',
        )
        # La valeur ne doit plus être en dur : elle vient de la variable dédiée.
        assert "var.alarm_treat_missing_data" in alarme
        assert 'treat_missing_data = "missing"' not in alarme

    def test_le_defaut_detecte_un_service_detruit(self):
        variables = (SOCLE / "variables.tf").read_text(encoding="utf-8")
        variable = _bloc(variables, 'variable "alarm_treat_missing_data"')
        assert re.search(r'default\s*=\s*"breaching"', variable), (
            "le défaut doit être breaching : avec missing, un service détruit reste "
            "en INSUFFICIENT_DATA et l'alarme ne se déclenche jamais"
        )
        # Le mode formation (missing) reste ouvert, mais borné par une validation.
        assert '"breaching", "missing"' in variable


class TestIsolationALB:
    """Revue 29/07, point 2 : un binôme pouvait supprimer la règle ALB d'un autre.

    La parade : règle et groupe de cibles précréés par le socle, et plus AUCUNE
    écriture elasticloadbalancing dans le rôle workstation.
    """

    def _team_hcl(self) -> str:
        return "\n".join(
            f.read_text(encoding="utf-8") for f in sorted(TEAM.glob("*.tf"))
        )

    def test_le_socle_precree_le_routage_de_chaque_binome(self):
        socle = (SOCLE / "alb_teams.tf").read_text(encoding="utf-8")
        for entete in (
            'resource "aws_lb_target_group" "team"',
            'resource "aws_lb_listener_rule" "team"',
        ):
            bloc = _bloc(socle, entete)
            assert "for_each = toset(var.teams)" in bloc, entete

    def test_le_module_team_ne_touche_plus_a_l_alb(self):
        team = self._team_hcl()
        assert 'resource "aws_lb_target_group"' not in team
        assert 'resource "aws_lb_listener_rule"' not in team
        # Le service s'accroche au groupe de cibles précréé, lu dans l'état du socle.
        assert "local.socle.target_group_arns[var.team_id]" in team

    def test_le_role_workstation_n_ecrit_plus_sur_l_alb(self):
        workstations = (SOCLE / "workstations.tf").read_text(encoding="utf-8")
        ecritures = [
            "elasticloadbalancing:CreateTargetGroup",
            "elasticloadbalancing:DeleteTargetGroup",
            "elasticloadbalancing:ModifyTargetGroup",
            "elasticloadbalancing:CreateRule",
            "elasticloadbalancing:DeleteRule",
            "elasticloadbalancing:ModifyRule",
            "elasticloadbalancing:SetRulePriorities",
        ]
        presentes = [a for a in ecritures if a in workstations]
        assert not presentes, (
            f"écritures ALB réapparues dans le rôle workstation : {presentes} — "
            "elles portent sur l'écouteur partagé et cassent l'isolation entre groupes"
        )
        # La lecture reste permise : diagnostic de santé des cibles au J2/J3.
        assert "elasticloadbalancing:Describe*" in workstations


class TestReseauSansInternet:
    """Revue 29/07, point 7 : l'environnement cible ne garantit pas la sortie internet.

    Les endpoints se créent par défaut et couvrent tout le parcours AWS.
    """

    def test_les_endpoints_sont_crees_par_defaut(self):
        variables = (SOCLE / "variables.tf").read_text(encoding="utf-8")
        variable = _bloc(variables, 'variable "enable_vpc_endpoints"')
        assert re.search(r"default\s*=\s*true", variable)

    def test_la_liste_couvre_le_parcours_complet(self):
        vpc = (SOCLE / "vpc.tf").read_text(encoding="utf-8")
        attendus = [
            "ecr.api",
            "ecr.dkr",
            "sagemaker.api",
            "sagemaker.runtime",
            "bedrock-runtime",
            "logs",
            "sts",
            "ssm",
            "ssmmessages",
            "ec2messages",
        ]
        manquants = [s for s in attendus if f'"{s}"' not in vpc]
        assert not manquants, f"endpoints d'interface manquants : {manquants}"
        # Le gateway S3, gratuit, reste inconditionnel.
        assert 'resource "aws_vpc_endpoint" "s3"' in vpc


class TestEnveloppeIAMWorkstation:
    """Constats du 10/08/2026, premier run complet depuis une VRAIE workstation.

    Trois droits manquaient au rôle apprenant ; chacun a cassé un geste du parcours.
    """

    def test_le_module_team_peut_gerer_son_groupe_de_logs(self):
        workstations = (SOCLE / "workstations.tf").read_text(encoding="utf-8")
        for action in ("logs:DeleteLogGroup", "logs:ListTagsForResource", "logs:ListTagsLogGroup"):
            assert action in workstations, (
                f"{action} manquant : team-apply/team-destroy échouent depuis la workstation"
            )

    def test_le_tag_de_sa_task_definition_est_permis(self):
        workstations = (SOCLE / "workstations.tf").read_text(encoding="utf-8")
        assert "task-definition/qc-${each.key}-*" in workstations, (
            "ecs:TagResource vise l'ARN de la task definition à l'enregistrement "
            "(default_tags) : sans lui, team-apply échoue depuis la workstation"
        )

    def test_mlflow_se_resout_par_nom(self):
        workstations = (SOCLE / "workstations.tf").read_text(encoding="utf-8")
        assert "sagemaker-mlflow:GetExperimentByName" in workstations, (
            "le client mlflow appelle experiments/get-by-name avant tout log_metric"
        )

    def test_la_sonde_logs_du_preflight_vit_dans_le_perimetre_apprenant(self):
        preflight = (SOCLE.parent.parent.parent / "scripts" / "preflight.py").read_text(
            encoding="utf-8"
        )
        assert '/ecs/qc-{config.team_id}-preflight-probe' in preflight
        assert '"/preflight/' not in preflight
