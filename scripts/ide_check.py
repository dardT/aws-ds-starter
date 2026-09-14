#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.32"]
# ///
"""
Vérifie que /healthz répond pour la passerelle IDE navigateur (code-server,
`infra/terraform/socle/ide.tf`) de chaque binôme — pour que le formateur découvre une
machine en panne la veille au soir plutôt qu'à 9h.

    make ide-check

Boucle sur `var.teams` en entier par défaut, comme `make ide-credentials`. Pendant un
test restreint (`ide_gateway_teams = ["g08"]` dans `ide.tf`), restreindre la même
liste ici avec IDE_TEAMS :

    IDE_TEAMS=g08 make ide-check

Sortie 0 si toutes les cibles répondent 200, 1 dès qu'une échoue.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import requests

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
        raise SystemExit(1)
    return json.loads(resultat.stdout)


def port_ide(team_id: str) -> int:
    return 10000 + int(team_id[1:3])


def main() -> int:
    dns = sortie_terraform("ide_alb_dns_name")
    if not dns:
        print("ide_alb_dns_name est vide — create_ide_gateway vaut false, rien à vérifier.", file=sys.stderr)
        return 1

    teams: list[str] = sortie_terraform("teams")
    filtre = os.environ.get("IDE_TEAMS", "").split()
    if filtre:
        teams = [t for t in teams if t in filtre]

    echecs: list[str] = []

    print(f"\n  Vérification de la passerelle IDE navigateur — {dns}\n")
    for team in teams:
        url = f"http://{dns}:{port_ide(team)}/healthz"
        try:
            reponse = requests.get(url, timeout=10)
            ok = reponse.status_code == 200
            detail = f"HTTP {reponse.status_code}"
        except requests.RequestException as exc:
            ok = False
            detail = str(exc)

        print(f"  {'✓' if ok else '✗'} {team:<8} {url}  ({detail})")
        if not ok:
            echecs.append(team)

    print()
    if echecs:
        print(f"{len(echecs)}/{len(teams)} équipe(s) en échec : {', '.join(echecs)}", file=sys.stderr)
        print("aws ssm start-associations-once --association-ids <id> pour rejouer une installation.", file=sys.stderr)
        return 1

    print(f"OK — les {len(teams)} passerelles répondent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
