"""Contrat du Dockerfile — revue du 29/07/2026, point 3.

La désactivation de CORS et de la protection XSRF était posée comme correctif normal.
Le montage nominal garde les protections actives et déclare l'origine externe
(STREAMLIT_BROWSER_SERVER_ADDRESS, passé par la définition de tâche) ; la désactivation
n'existe plus que derrière SANDBOX_RELAX_WEBSOCKET=1, comme dérogation sandbox.
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def test_les_protections_ne_sont_desactivees_que_par_derogation():
    dockerfile = (RACINE / "Dockerfile").read_text(encoding="utf-8")
    cmd = dockerfile[dockerfile.index('CMD ["sh", "-c"') :]

    assert "SANDBOX_RELAX_WEBSOCKET" in cmd, "la dérogation sandbox doit rester possible"
    # Les deux drapeaux ne vivent QUE dans la branche de dérogation, jamais sur la
    # ligne de commande inconditionnelle.
    branche_relax = cmd[cmd.index("RELAX='") : cmd.index("';")]
    commande = cmd[cmd.index("exec uv run") :]
    assert "--server.enableCORS=false" in branche_relax
    assert "--server.enableXsrfProtection=false" in branche_relax
    assert "enableCORS" not in commande
    assert "enableXsrfProtection" not in commande


def test_la_definition_de_tache_declare_l_origine_externe():
    ecs = (RACINE / "infra" / "terraform" / "team" / "ecs.tf").read_text(encoding="utf-8")
    assert "STREAMLIT_BROWSER_SERVER_ADDRESS" in ecs
    assert "alb_dns_name" in ecs
