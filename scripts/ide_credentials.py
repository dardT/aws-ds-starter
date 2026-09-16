#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3>=1.35"]
# ///
"""
Génère le tableau TEAM_ID → URL → mot de passe de la passerelle IDE navigateur
(code-server, `infra/terraform/socle/ide.tf`), à distribuer au lancement de la
formation.

Même esprit que `make backend-hcl` : rien n'est recopié à la main depuis la console.
Depuis la décision D28 (15/09/2026), la passerelle est servie en HTTPS sur un domaine
possédé (`ide_domain_name`, par ex. `vsc0de.fr`) et route par CHEMIN littéral —
`https://vsc0de.fr/g01/` — au lieu du port 10000 + les deux chiffres du TEAM_ID
d'avant. Le domaine est lu dans les sorties Terraform, jamais codé ici.

    make ide-credentials

Boucle sur `var.teams` en entier par défaut : à lancer une fois `create_ide_gateway`
élargi à toutes les équipes (PLAN.md, Rollout order, étape 5) — une équipe sans
passerelle encore créée y échouerait sur un paramètre SSM introuvable.

Pendant un test restreint (`ide_gateway_teams = ["g08"]` dans `ide.tf`), restreindre
la même liste ici avec IDE_TEAMS, sans quoi le script échoue sur les équipes non
provisionnées :

    IDE_TEAMS=g08 make ide-credentials
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
TF_SOCLE = ROOT / "infra" / "terraform" / "socle"


def sortie_terraform(cle: str) -> object:
    resultat = subprocess.run(
        ["terraform", "output", "-json", cle],
        cwd=TF_SOCLE,
        capture_output=True,
        text=True,
    )
    if resultat.returncode != 0:
        print(f"Impossible de lire la sortie Terraform « {cle} » : {resultat.stderr.strip()}", file=sys.stderr)
        print("Le socle a-t-il été appliqué avec create_ide_gateway = true ? (socle-apply)", file=sys.stderr)
        raise SystemExit(1)
    return json.loads(resultat.stdout)


def url_ide(domaine: str, team_id: str) -> str:
    # Chemin littéral, barre oblique finale comprise : c'est elle que nginx attend sur
    # la machine (`location /<TEAM_ID>/`, ide_setup.sh.tftpl). Sans elle, l'ALB route
    # quand même (les deux motifs sont dans la règle) mais nginx répond une 301 — autant
    # distribuer directement l'URL définitive.
    return f"https://{domaine}/{team_id}/"


def main() -> int:
    domaine = sortie_terraform("ide_domain_name")
    if not domaine:
        print("ide_domain_name est vide — create_ide_gateway vaut false, rien à distribuer.", file=sys.stderr)
        return 1

    teams: list[str] = sortie_terraform("teams")
    filtre = os.environ.get("IDE_TEAMS", "").split()
    if filtre:
        teams = [t for t in teams if t in filtre]

    region = os.environ.get("AWS_REGION", "eu-west-3")
    ssm = boto3.client("ssm", region_name=region)

    print(f"\n  Passerelle IDE navigateur — https://{domaine}/\n")
    print(f"  {'TEAM_ID':<8} {'URL':<32} MOT DE PASSE")

    echecs: list[str] = []
    for team in teams:
        url = url_ide(domaine, team)
        try:
            reponse = ssm.get_parameter(Name=f"/qc/{team}/code-server-password", WithDecryption=True)
            mot_de_passe = reponse["Parameter"]["Value"]
        except ClientError as exc:
            echecs.append(team)
            print(f"  {team:<8} {url:<32} INTROUVABLE ({exc.response['Error']['Code']})")
            continue
        print(f"  {team:<8} {url:<32} {mot_de_passe}")

    print()
    if echecs:
        print(f"{len(echecs)} équipe(s) sans mot de passe : {', '.join(echecs)}", file=sys.stderr)
        print("La passerelle a-t-elle bien été élargie à ces équipes ? (workstation_teams)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
