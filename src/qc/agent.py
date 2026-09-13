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
    return '"name"' in reponse and '"arguments"' in reponse and (
        '[{' in reponse or '{"' in reponse
    )


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
        return "predire_conformite" in self.outils_appeles

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
    from strands import tool

    @tool
    def predire_conformite(indices: list[int]) -> str:
        """Interroge le modèle de contrôle qualité sur des pièces de l'échantillon.

        Args:
            indices: Numéros des pièces à évaluer, à partir de 0.
        """
        appeles.append("predire_conformite")
        from qc import inference

        _, lignes = inference.read_sample(inference.local_sample())

        hors_bornes = [i for i in indices if i < 0 or i >= len(lignes)]
        if hors_bornes:
            return (
                f"Pièces inexistantes : {hors_bornes}. "
                f"L'échantillon contient les pièces 0 à {len(lignes) - 1}."
            )

        predictions = inference.predict([lignes[i] for i in indices])
        # Le seuil vient de l'artefact d'entraînement (F2 sur le train). Le donner au
        # modèle dans la réponse de l'outil lui évite d'en inventer un.
        seuil = inference.decision_threshold()
        lignes_reponse = [f"seuil de décision : {seuil:.2f}"]
        lignes_reponse += [
            f"pièce {indices[p.index]} : score {p.score:.4f} ({p.label(seuil)})"
            for p in predictions
        ]
        return "\n".join(lignes_reponse)

    @tool
    def lire_mesures(indice: int, nombre: int = 5) -> str:
        """Donne les premières mesures capteurs d'une pièce de l'échantillon.

        Args:
            indice: Numéro de la pièce, à partir de 0.
            nombre: Combien de mesures retourner.
        """
        appeles.append("lire_mesures")
        from qc import inference

        entete, lignes = inference.read_sample(inference.local_sample())
        if indice < 0 or indice >= len(lignes):
            return f"Pièce inexistante. L'échantillon contient les pièces 0 à {len(lignes) - 1}."

        return "\n".join(
            f"{nom} = {valeur:.4f}"
            for nom, valeur in list(zip(entete, lignes[indice]))[:nombre]
        )

    @tool
    def comparer_a_l_echantillon(indice: int) -> str:
        """Situe une pièce par rapport aux autres pièces de l'échantillon.

        Args:
            indice: Numéro de la pièce, à partir de 0.
        """
        appeles.append("comparer_a_l_echantillon")
        from qc import inference

        _, lignes = inference.read_sample(inference.local_sample())
        if indice < 0 or indice >= len(lignes):
            return f"Pièce inexistante. L'échantillon contient les pièces 0 à {len(lignes) - 1}."

        predictions = inference.predict(lignes)
        scores = sorted((p.score for p in predictions), reverse=True)
        cible = predictions[indice].score
        rang = scores.index(cible) + 1

        return (
            f"Pièce {indice} : score {cible:.4f}, rang {rang} sur {len(scores)} "
            f"(le plus élevé est {scores[0]:.4f}, le plus faible {scores[-1]:.4f})."
        )

    @tool
    def expliquer_le_score(indice: int) -> str:
        """Dit quelles mesures pèsent dans le score d'une pièce, et dans quel sens.

        SEULE source valable pour justifier ou expliquer un score : les noms de capteurs
        et leurs contributions viennent d'ici, jamais d'ailleurs. À appeler pour toute
        question du type « pourquoi », « justifie », « quels capteurs ».

        Args:
            indice: Numéro de la pièce, à partir de 0.
        """
        appeles.append("expliquer_le_score")
        from qc import explain, inference

        entete, lignes = inference.read_sample(inference.local_sample())
        if indice < 0 or indice >= len(lignes):
            return f"Pièce inexistante. L'échantillon contient les pièces 0 à {len(lignes) - 1}."

        # L'explication a besoin du modèle lui-même, pas de l'endpoint : elle télécharge
        # l'artefact du dernier entraînement. Cet appel peut échouer là où les trois
        # autres outils réussissent — droits SageMaker manquants sur ListTrainingJobs,
        # artefact absent, pipeline illisible par joblib.
        #
        # La capture est VOLONTAIREMENT large. Un tool use est la frontière où une
        # exception ne sert à personne : elle remonte dans la boucle Strands sous forme
        # de trace Python, que le modèle ne sait pas exploiter. Les trois échecs ci-dessus
        # lèvent trois types différents — ExplainError, ClientError de botocore, une
        # erreur de désérialisation joblib — et les énumérer laisserait toujours passer
        # le quatrième.
        try:
            explication = explain.explain_row(lignes[indice], entete, indice=indice)
        except Exception as exc:  # noqa: BLE001
            return f"Explication indisponible : {type(exc).__name__} — {exc}"

        return explication.summary()

    return [predire_conformite, lire_mesures, comparer_a_l_echantillon, expliquer_le_score]


# --------------------------------------------------------------------------- agent


def build(appeles: list[str] | None = None):
    """Construit l'agent. Renvoie l'objet Strands, sans l'interroger.

    Séparé de `ask()` pour que l'application Streamlit le construise une fois au
    démarrage plutôt qu'à chaque question : la construction ouvre une session boto3.
    """
    try:
        from strands import Agent
        from strands.models import BedrockModel
    except ImportError as exc:
        raise AgentError(
            "Le SDK Strands n'est pas installé.\n"
            "  Corriger : `make sync`, qui installe depuis uv.lock."
        ) from exc

    if not config.bedrock_model_id:
        raise AgentError(
            "BEDROCK_MODEL_ID est vide dans .env.\n"
            "  Valeur attendue : mistral.mistral-large-2402-v1:0 (décision D14)."
        )

    appeles = appeles if appeles is not None else []
    objet = Agent(
        model=BedrockModel(
            model_id=config.bedrock_model_id,
            region_name=config.region,
            # D15 : en streaming, le tool use casse sur ce modèle. L'agent répond alors
            # sans jamais appeler d'outil, ce qui ressemble à un modèle peu coopératif
            # plutôt qu'à un défaut de configuration.
            streaming=False,
            temperature=TEMPERATURE,
            max_tokens=config.bedrock_max_tokens,
        ),
        tools=_outils(appeles),
        system_prompt=SYSTEM_PROMPT,
        # Par défaut, Strands imprime la réponse et chaque appel d'outil sur la sortie
        # standard. Le module deviendrait bavard, la trace de `check_day2` illisible, et
        # l'application Streamlit afficherait sa réponse deux fois. On récupère le texte
        # par la valeur de retour, pas par ce qui a été imprimé.
        callback_handler=None,
    )

    # La liste des appels est attachée à l'agent. Sans cela, `ask(agent=objet)` ne peut
    # pas la retrouver : il en crée une nouvelle, que les outils n'alimentent jamais, et
    # renvoie une trace VIDE alors que des outils ont bel et bien été appelés.
    #
    # Conséquence si on ne le fait pas : l'application affiche « aucun outil appelé » et
    # avertit que la réponse est inventée, sur une réponse parfaitement fondée. C'est
    # l'inverse exact de ce que la trace doit servir à détecter.
    objet._qc_appels = appeles
    return objet


def ask(question: str, agent=None) -> Trace:
    """Pose une question à l'agent et renvoie sa réponse avec les outils appelés."""
    if agent is None:
        appeles: list[str] = []
        agent = build(appeles)
    else:
        # Agent construit par l'appelant — l'application Streamlit le fait une fois au
        # démarrage. On récupère SA liste d'appels, pas une neuve.
        appeles = getattr(agent, "_qc_appels", [])
        appeles.clear()

        # Chaque question repart d'une conversation VIERGE.
        #
        # Mesuré le 28/07/2026 : sur une question de suivi (« Et la pièce 17 ? »), le
        # modèle réutilise le format de la réponse précédente et énonce un score SANS
        # appeler l'outil — 0,1248 annoncé là où l'endpoint renvoie 0,4642. Le garde-fou
        # du prompt système ne tient plus une fois l'historique chargé d'exemples.
        #
        # L'interface ne montre pas de conversation, juste une question et une réponse :
        # la rendre sans état correspond à ce que l'utilisateur voit, et supprime le
        # problème au lieu de le rattraper.
        if hasattr(agent, "messages"):
            agent.messages.clear()

    try:
        reponse = agent(question)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "AccessDenied" in message or "not authorized" in message:
            raise AgentError(
                f"Bedrock refuse le modèle {config.bedrock_model_id}.\n"
                "  L'accès au modèle se demande dans la console Bedrock, et la présence\n"
                "  au catalogue ne vaut pas accès (décision D6).\n"
                "  Vérifier : contrôle n°7 du preflight."
            ) from exc
        if "ThrottlingException" in message or "TooManyRequests" in message:
            raise AgentError(
                "Bedrock limite le débit. Sur un compte partagé, six binômes qui\n"
                "  interrogent l'agent en même temps saturent le quota par minute.\n"
                "  Corriger : relancer dans quelques secondes."
            ) from exc
        raise AgentError(f"L'agent a échoué : {type(exc).__name__} — {message}") from exc

    texte = str(reponse).strip()

    # Un seul nouvel essai. Le défaut est intermittent : redemander suffit presque
    # toujours. Insister davantage coûterait des tokens sans rien changer, et masquerait
    # un vrai problème de configuration.
    if _est_un_appel_en_texte(texte):
        try:
            texte = str(
                agent(
                    "Ta réponse précédente contenait un appel d'outil au format JSON au "
                    "lieu de l'exécuter. Exécute l'outil, puis réponds en français."
                )
            ).strip()
        except Exception:  # noqa: BLE001
            pass

    if _est_un_appel_en_texte(texte):
        raise AgentError(
            "Le modèle a écrit son appel d'outil au lieu de l'exécuter, deux fois de"
            " suite.\n"
            "  C'est un comportement intermittent de ce modèle sur les questions qui"
            " enchaînent\n"
            "  plusieurs outils. Corriger : reposer la question en une seule demande à la"
            " fois."
        )

    return Trace(
        question=question,
        reponse=texte,
        outils_appeles=tuple(dict.fromkeys(appeles)),
    )
