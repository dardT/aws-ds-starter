"""J2 · Bedrock — agent outillé qui explique une décision de contrôle qualité.

Ce module ne fait AUCUN affichage : il calcule, il renvoie. L'affichage est dans `qc.cli`
et dans l'application Streamlit.

Le J1 s'arrêtait sur un score. Un opérateur qui lit « 0,4642 » ne sait pas quoi en faire :
il lui manque le seuil, la comparaison aux autres pièces, et ce que mesurent les capteurs
qui pèsent dans la décision. L'agent comble cet écart en appelant lui-même les outils dont
il a besoin.

    from qc import agent
    reponse = agent.ask("La pièce 13 est-elle conforme ?")

Trois contraintes vérifiées contre le compte réel, et chacune casse tout si on l'ignore :

  - le modèle est `mistral.mistral-large-2402-v1:0` (D14). Nova et tous les modèles
    Anthropic figurent au catalogue et renvoient `AccessDenied` ;
  - `BedrockModel(..., streaming=False)` (D15). En streaming, le tool use casse sur ce
    modèle ;
  - l'appel d'outil n'est pas garanti. Le prompt système doit l'imposer, sinon le modèle
    invente un score plausible au lieu d'interroger l'endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from qc.config import DATA_DIR, config

#: Température basse. On veut une réponse stable sur une même question : deux binômes qui
#: posent la même question doivent voir la même chose, et le J3 compare des traces.
TEMPERATURE = 0.2

#: Le prompt système. Il porte deux obligations dont dépend tout le lab.
#:
#: 1. INTERDIRE l'invention d'un score. Sans cette phrase, le modèle répond « environ
#:    0,3 » avec aplomb, sans avoir appelé quoi que ce soit — et la démonstration
#:    ressemble à un succès.
#: 2. Rappeler que le seuil appartient au métier. C'est la question laissée ouverte au J1.
SYSTEM_PROMPT = """Tu es un assistant de contrôle qualité sur une ligne de production de \
semi-conducteurs. Tu aides un opérateur à décider si une pièce doit être écartée.

Règles absolues :
- Tu ne donnes JAMAIS de score que tu n'as pas obtenu par l'outil `predire_conformite`. \
Si tu ne peux pas appeler l'outil, tu le dis, tu n'estimes pas.
- Le score est une PROBABILITÉ de non-conformité, pas une décision. Le seuil de décision \
appartient au métier : écarter une bonne pièce et laisser passer une pièce défectueuse \
n'ont pas le même coût.
- Quand un score est proche du seuil, tu le signales explicitement plutôt que de trancher.
- Tu ne nommes JAMAIS un capteur ni une contribution que tu n'as pas obtenus par l'outil \
`expliquer_le_score`. Toute demande de justification — « pourquoi », « justifie », \
« explique », « quels capteurs » — exige d'appeler `expliquer_le_score` AVANT de répondre. \
`lire_mesures` donne des valeurs brutes, il ne dit pas ce qui a pesé dans la décision.

Tu réponds en français, brièvement, en citant les valeurs exactes que les outils te \
renvoient."""


def _est_un_appel_en_texte(reponse: str) -> bool:
    """Le modèle a-t-il écrit son appel d'outil au lieu de l'exécuter ?

    Comportement intermittent de Mistral sur Bedrock, observé le 28/07/2026 : au lieu
    d'émettre un bloc d'appel d'outil, le modèle répond littéralement

        [{"name": "lire_mesures", "arguments": {"indice": 13, "nombre": 5}}]

    Strands n'y voit qu'une réponse textuelle, n'exécute rien, et l'utilisateur reçoit du
    JSON en guise de réponse. Ça survient surtout sur les questions qui enchaînent
    plusieurs outils.
    """
    # TODO-D2-02 — à écrire.
    raise NotImplementedError("TODO-D2-02")


class AgentError(RuntimeError):
    """Erreur d'agent, formulée pour être lue par un apprenant."""


@dataclass(frozen=True)
class Trace:
    """Ce que l'agent a fait pour répondre.

    Les outils appelés comptent autant que la réponse : c'est ce qui distingue un agent
    qui a interrogé l'endpoint d'un modèle qui a inventé une valeur vraisemblable. Le J3
    s'en sert pour diagnostiquer une réponse suspecte.
    """

    question: str
    reponse: str
    outils_appeles: tuple[str, ...]

    @property
    def a_consulte_le_modele(self) -> bool:
        # TODO-D2-03 — à écrire.
        raise NotImplementedError("TODO-D2-03")

    def summary(self) -> str:
        outils = ", ".join(self.outils_appeles) if self.outils_appeles else "aucun"
        return f"{self.reponse}\n\n  outils appelés : {outils}"


# --------------------------------------------------------------------------- outils
#
# Chaque outil est une fonction Python ordinaire. Strands lit sa signature et sa
# docstring pour construire le schéma envoyé au modèle : la docstring n'est donc pas de
# la documentation, c'est l'interface. Une description vague produit un outil que le
# modèle n'appelle jamais, ou appelle à tort.


def _outils(appeles: list[str]):
    """Construit les outils, en enregistrant leurs appels dans `appeles`."""
    # TODO-D2-04 — à écrire.
    raise NotImplementedError("TODO-D2-04")


# --------------------------------------------------------------------------- agent


def build(appeles: list[str] | None = None):
    """Construit l'agent. Renvoie l'objet Strands, sans l'interroger.

    Séparé de `ask()` pour que l'application Streamlit le construise une fois au
    démarrage plutôt qu'à chaque question : la construction ouvre une session boto3.
    """
    # TODO-D2-05 — à écrire.
    raise NotImplementedError("TODO-D2-05")


def ask(question: str, agent=None) -> Trace:
    """Pose une question à l'agent et renvoie sa réponse avec les outils appelés."""
    # TODO-D2-06 — à écrire.
    raise NotImplementedError("TODO-D2-06")
