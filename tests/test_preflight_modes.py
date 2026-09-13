"""Modes du preflight — revue du 29/07/2026, point 5.

Le contrat qui compte : le mode par défaut (apprenant) n'émet AUCUN appel d'écriture
IAM. La sonde iam:CreateRole — trace CloudTrail, alertes sécurité — n'existe qu'en
`--formateur`, seul profil qui a besoin de ce droit (make socle-apply).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

ECRITURES_IAM = {
    "create_role",
    "delete_role",
    "put_role_policy",
    "attach_role_policy",
    "create_policy",
    "create_user",
}


@pytest.fixture(scope="module")
def preflight():
    """Charge scripts/preflight.py comme un module, une fois pour toutes."""
    spec = importlib.util.spec_from_file_location(
        "preflight", RACINE / "scripts" / "preflight.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["preflight"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def resultats_vierges(preflight):
    preflight.results.clear()
    yield preflight.results
    preflight.results.clear()


ARN = "arn:aws:iam::123456789012:role/apprenant-g01"


def test_le_mode_apprenant_n_emet_aucune_ecriture_iam(preflight, aws, resultats_vierges):
    aws.repondre(
        "iam",
        "simulate_principal_policy",
        {
            "EvaluationResults": [
                {"EvalActionName": a, "EvalDecision": "allowed"}
                for a in preflight.REQUIRED_ACTIONS
            ]
        },
    )

    preflight.check_permissions(ARN)  # sans --formateur : mode par défaut

    emises = set(aws.operations("iam"))
    assert emises == {"simulate_principal_policy"}
    assert not (emises & ECRITURES_IAM)
    assert resultats_vierges[0].status == preflight.PASS


def test_le_mode_apprenant_reste_muet_meme_sans_simulation(preflight, aws, resultats_vierges):
    """Le cas SSO : la simulation échoue. L'ancien code basculait alors sur la sonde
    create_role — exactement ce que le mode apprenant doit s'interdire."""
    aws.echouer("iam", "simulate_principal_policy", "AccessDenied")

    preflight.check_permissions(ARN)

    assert set(aws.operations("iam")) == {"simulate_principal_policy"}
    # Sans sonde, pas de verdict : SKIP, jamais un FAIL fondé sur une simulation qui ment.
    assert resultats_vierges[0].status == preflight.SKIP


def test_le_mode_formateur_garde_sa_sonde_reelle(preflight, aws, resultats_vierges):
    aws.echouer("iam", "simulate_principal_policy", "AccessDenied")
    aws.echouer("iam", "create_role", "MalformedPolicyDocument")

    preflight.check_permissions(ARN, formateur=True)

    assert "create_role" in aws.operations("iam")
    assert resultats_vierges[0].status == preflight.PASS


def test_un_refus_formateur_reste_un_echec_actionnable(preflight, aws, resultats_vierges):
    aws.echouer("iam", "simulate_principal_policy", "AccessDenied")
    aws.echouer("iam", "create_role", "AccessDenied")

    preflight.check_permissions(ARN, formateur=True)

    assert resultats_vierges[0].status == preflight.FAIL
    assert "socle" in resultats_vierges[0].cause
