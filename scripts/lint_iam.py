#!/usr/bin/env python3
"""Lint IAM — chaque `resources = ["*"]` doit porter sa justification, sur sa ligne.

Revue du 29/07/2026, point 8. Le point 2 a montré le coût d'un joker non questionné :
les écritures elasticloadbalancing sur `*` permettaient à un binôme de supprimer la
règle ALB d'un autre. Ce lint ne les interdit pas — certaines actions AWS n'acceptent
aucune restriction par ressource — il exige que chacun soit une DÉCISION écrite :

    resources = ["*"] // GetMetricData n'accepte pas de restriction par ressource

Un joker sans commentaire en fin de ligne est une erreur. La justification vit sur la
ligne même, pas dans un prose au-dessus : c'est ce qui la rend vérifiable, et ce qui
oblige à la réécrire quand on copie-colle le statement.

    uv run scripts/lint_iam.py            # fait partie de `make ci`

Sortie 0 si tous les jokers sont justifiés, 1 sinon.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERRAFORM = ROOT / "infra" / "terraform"

#: Un joker de ressources, suivi — ou non — d'un commentaire de justification.
JOKER = re.compile(r'^\s*resources\s*=\s*\[\s*"\*"\s*\]\s*(?P<justification>//.*)?$')


def violations(racine: Path = TERRAFORM) -> list[str]:
    """Les `resources = ["*"]` sans justification, au format fichier:ligne."""
    sans_justification: list[str] = []
    for fichier in sorted(racine.rglob("*.tf")):
        for numero, ligne in enumerate(
            fichier.read_text(encoding="utf-8").splitlines(), start=1
        ):
            correspondance = JOKER.match(ligne)
            if correspondance and not correspondance.group("justification"):
                chemin = fichier.relative_to(ROOT) if fichier.is_relative_to(ROOT) else fichier
                sans_justification.append(f"{chemin}:{numero}")
    return sans_justification


def main() -> int:
    fautes = violations()
    if fautes:
        print("resources = [\"*\"] sans justification en fin de ligne :\n")
        for faute in fautes:
            print(f"  {faute}")
        print(
            "\nSoit restreindre par ARN/préfixe/condition, soit écrire pourquoi c'est"
            "\nimpossible — sur la ligne même : resources = [\"*\"] // <raison>"
        )
        return 1
    print("lint IAM : tous les jokers de ressources sont justifiés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
