"""Tests de `qc.agent`.

Aucun appel à Bedrock : un appel réel coûte des tokens, dépend du réseau, et surtout ne
donne pas deux fois la même réponse — un test qui asserte sur du texte généré échoue au
hasard. Ce qui est vérifié ici, c'est ce qui casse silencieusement : les contraintes du
modèle, le comportement des outils, et la trace qui distingue une vraie prédiction d'une
valeur inventée.

L'agent réel est vérifié par `uv run qc agent` et `make check-day2`.
"""

from __future__ import annotations

import pytest

from qc import agent, inference


@pytest.fixture
def echantillon(tmp_path, monkeypatch):
    """Un échantillon minuscule, à la place de data/sample.csv."""
    fichier = tmp_path / "sample.csv"
    fichier.write_text("sensor_000,sensor_001\n1.0,2.0\n3.0,4.0\n5.0,6.0\n")
    # Le chemin de l'échantillon est décidé par `inference.local_sample()`, point
    # unique partagé par l'application et par les outils de l'agent.
    monkeypatch.setattr(inference, "DATA_DIR", tmp_path)
    return fichier


@pytest.fixture
def predictions_factices(monkeypatch):
    """Remplace l'appel à l'endpoint par des scores fixes — et le seuil par une
    constante : `decision_threshold()` télécharge sinon l'artefact réel depuis S3, et
    la suite se remettrait à joindre AWS sans le dire (décision D24)."""

    def predict(rows, endpoint_name=None):
        return [inference.Prediction(index=i, score=0.1 * (i + 1)) for i in range(len(rows))]

    monkeypatch.setattr(inference, "predict", predict)
    monkeypatch.setattr(inference, "decision_threshold", lambda: 0.58)


def outils(appeles):
    return {t.tool_name: t for t in agent._outils(appeles)}


def test_le_prompt_interdit_d_inventer_un_score():
    """Sans cette consigne, le modèle répond « environ 0,3 » avec aplomb sans avoir rien
    appelé — et la démonstration ressemble à un succès."""
    assert "JAMAIS" in agent.SYSTEM_PROMPT
    assert "predire_conformite" in agent.SYSTEM_PROMPT


def test_le_prompt_rappelle_que_le_seuil_appartient_au_metier():
    assert "PROBABILITÉ" in agent.SYSTEM_PROMPT
    assert "seuil" in agent.SYSTEM_PROMPT


def test_la_temperature_est_basse():
    """Deux binômes qui posent la même question doivent voir la même chose, et le J3
    compare des traces d'une exécution à l'autre."""
    assert agent.TEMPERATURE <= 0.3


def test_predire_conformite_interroge_le_modele(echantillon, predictions_factices):
    appeles: list[str] = []
    resultat = outils(appeles)["predire_conformite"](indices=[0, 2])

    assert appeles == ["predire_conformite"]
    assert "pièce 0" in resultat and "pièce 2" in resultat
    assert "0.1000" in resultat


def test_un_indice_hors_bornes_repond_sans_lever(echantillon, predictions_factices):
    """Le modèle choisit lui-même ses arguments. Une exception Python remonterait dans
    la boucle de l'agent sous forme de trace, que le modèle ne sait pas exploiter ; une
    phrase lui permet de se corriger tout seul."""
    resultat = outils([])["predire_conformite"](indices=[99])

    assert "inexistantes" in resultat
    assert "0 à 2" in resultat


def test_lire_mesures_nomme_les_capteurs(echantillon):
    resultat = outils([])["lire_mesures"](indice=1, nombre=2)

    assert "sensor_000 = 3.0000" in resultat
    assert "sensor_001 = 4.0000" in resultat


def test_comparer_situe_la_piece_dans_l_echantillon(echantillon, predictions_factices):
    resultat = outils([])["comparer_a_l_echantillon"](indice=2)

    assert "rang 1 sur 3" in resultat, resultat


def test_les_quatre_outils_sont_declares(echantillon):
    """Une fonction définie dans `_outils` mais absente de la liste renvoyée laisse tous
    les autres tests au vert, et le modèle ne voit jamais l'outil."""
    assert set(outils([])) == {
        "predire_conformite",
        "lire_mesures",
        "comparer_a_l_echantillon",
        "expliquer_le_score",
    }


def test_expliquer_le_score_nomme_les_mesures_determinantes(echantillon, monkeypatch):
    from qc import explain

    def explain_row(mesures, noms, indice=0, **_):
        return explain.Explication(
            indice=indice,
            contributions=(explain.Contribution("sensor_001", 4.0, 0.7),),
            biais=-0.2,
        )

    monkeypatch.setattr(explain, "explain_row", explain_row)

    appeles: list[str] = []
    resultat = outils(appeles)["expliquer_le_score"](indice=1)

    assert appeles == ["expliquer_le_score"]
    assert "sensor_001" in resultat
    assert "NON CONFORME" in resultat


def test_un_artefact_absent_donne_une_phrase_et_non_une_exception(echantillon, monkeypatch):
    """L'explication a besoin du modèle, là où les trois autres outils se contentent de
    l'endpoint : elle peut échouer seule. Une exception remonterait dans la boucle Strands
    sous forme de trace, que le modèle ne sait pas exploiter."""
    from qc import explain

    def explain_row(*_, **__):
        raise explain.ExplainError("Artefact du modèle introuvable")

    monkeypatch.setattr(explain, "explain_row", explain_row)
    resultat = outils([])["expliquer_le_score"](indice=0)

    assert "indisponible" in resultat
    assert "introuvable" in resultat


def test_une_erreur_non_prevue_donne_aussi_une_phrase(echantillon, monkeypatch):
    """Trois échecs plausibles lèvent trois types différents : ExplainError si l'artefact
    manque, ClientError si le rôle n'a pas ListTrainingJobs, XGBoostError si l'archive est
    illisible. Les énumérer laisserait toujours passer le quatrième."""
    from qc import explain

    def explain_row(*_, **__):
        raise RuntimeError("AccessDeniedException: sagemaker:ListTrainingJobs")

    monkeypatch.setattr(explain, "explain_row", explain_row)
    resultat = outils([])["expliquer_le_score"](indice=0)

    assert "indisponible" in resultat
    assert "ListTrainingJobs" in resultat


def test_le_prompt_oriente_le_pourquoi_vers_l_explication():
    """Sans cette consigne, le modèle appelle `lire_mesures` et narre des valeurs brutes
    au lieu de dire ce qui a pesé dans la décision."""
    assert "expliquer_le_score" in agent.SYSTEM_PROMPT


def test_la_trace_distingue_une_prediction_d_une_invention():
    """C'est le seul moyen de savoir si l'agent a vraiment interrogé l'endpoint. Un
    modèle qui répond sans appeler d'outil produit un texte tout aussi crédible."""
    avec = agent.Trace("q", "r", ("predire_conformite",))
    sans = agent.Trace("q", "r", ())

    assert avec.a_consulte_le_modele
    assert not sans.a_consulte_le_modele
    assert "aucun" in sans.summary()


def test_un_modele_non_configure_dit_quoi_faire(monkeypatch):
    monkeypatch.setattr(agent.config, "bedrock_model_id", "", raising=False)
    with pytest.raises(agent.AgentError) as exc:
        agent.build()

    assert "BEDROCK_MODEL_ID" in str(exc.value)
    assert "D14" in str(exc.value)


def test_un_acces_refuse_renvoie_vers_le_preflight(monkeypatch):
    """La présence au catalogue ne vaut pas accès (D6). Le message brut de boto3 ne le
    dit pas, et c'est l'erreur numéro un du J2."""

    class AgentQuiRefuse:
        def __call__(self, question):
            raise RuntimeError("AccessDeniedException: not authorized to invoke")

    monkeypatch.setattr(agent.config, "bedrock_model_id", "mistral.x", raising=False)
    with pytest.raises(agent.AgentError) as exc:
        agent.ask("test", agent=AgentQuiRefuse())

    assert "n°7" in str(exc.value)


def test_une_limitation_de_debit_est_reconnue(monkeypatch):
    """Six binômes qui interrogent l'agent en même temps saturent le quota par minute.
    Sans ce message, ils croient à une panne de leur code."""

    class AgentSature:
        def __call__(self, question):
            raise RuntimeError("ThrottlingException: rate exceeded")

    with pytest.raises(agent.AgentError) as exc:
        agent.ask("test", agent=AgentSature())

    assert "débit" in str(exc.value)


def test_ask_renvoie_une_trace_exploitable():
    class AgentMuet:
        def __call__(self, question):
            return "  La pièce 13 est signalée.  "

    trace = agent.ask("La pièce 13 est-elle conforme ?", agent=AgentMuet())

    assert trace.reponse == "La pièce 13 est signalée."
    assert trace.question.startswith("La pièce 13")
    assert trace.outils_appeles == ()


def test_un_agent_construit_a_l_avance_garde_sa_trace(monkeypatch):
    """Bug réel, constaté le 28/07/2026 sur le chemin de l'application Streamlit.

    `ask(agent=objet)` créait une liste d'appels NEUVE, que les outils de l'agent
    n'alimentaient jamais : la trace revenait vide alors que des outils avaient été
    appelés. L'application affichait alors « aucun outil appelé » et avertissait que la
    réponse était inventée — sur une réponse parfaitement fondée.
    """

    class AgentQuiAppelleUnOutil:
        def __init__(self):
            self._qc_appels = []

        def __call__(self, question):
            self._qc_appels.append("predire_conformite")
            return "La pièce 13 est signalée."

    trace = agent.ask("La pièce 13 ?", agent=AgentQuiAppelleUnOutil())

    assert trace.outils_appeles == ("predire_conformite",)
    assert trace.a_consulte_le_modele


def test_les_appels_d_une_question_precedente_ne_debordent_pas():
    """L'application construit l'agent une seule fois et le réutilise. Sans remise à
    zéro, la deuxième question hériterait des outils de la première."""

    class AgentReutilise:
        def __init__(self):
            self._qc_appels = ["lire_mesures"]

        def __call__(self, question):
            self._qc_appels.append("predire_conformite")
            return "réponse"

    trace = agent.ask("question", agent=AgentReutilise())
    assert trace.outils_appeles == ("predire_conformite",)


def test_un_appel_d_outil_ecrit_en_texte_est_reconnu():
    """Comportement intermittent de Mistral sur Bedrock : au lieu d'émettre un bloc
    d'appel d'outil, le modèle répond littéralement le JSON de l'appel. Strands n'y voit
    qu'un texte, n'exécute rien, et l'utilisateur reçoit du JSON en guise de réponse."""
    brut = '[{"name": "lire_mesures", "arguments": {"indice": 13, "nombre": 5}}]'

    assert agent._est_un_appel_en_texte(brut)
    assert not agent._est_un_appel_en_texte("La pièce 13 est suspecte, score 0.9183.")
    assert not agent._est_un_appel_en_texte("")


def test_un_appel_en_texte_declenche_un_seul_nouvel_essai():
    class AgentBavard:
        def __init__(self):
            self.appels = 0
            self._qc_appels = []

        def __call__(self, question):
            self.appels += 1
            if self.appels == 1:
                return '[{"name": "lire_mesures", "arguments": {"indice": 13}}]'
            return "La pièce 13 a un score de 0.9183."

    objet = AgentBavard()
    trace = agent.ask("La pièce 13 ?", agent=objet)

    assert objet.appels == 2
    assert "0.9183" in trace.reponse


def test_deux_echecs_de_suite_donnent_un_message_actionnable():
    class AgentTetu:
        _qc_appels: list[str] = []

        def __call__(self, question):
            return '[{"name": "lire_mesures", "arguments": {"indice": 13}}]'

    with pytest.raises(agent.AgentError) as exc:
        agent.ask("La pièce 13 ?", agent=AgentTetu())

    assert "au lieu de l'exécuter" in str(exc.value)


def test_un_appel_en_texte_est_reconnu_meme_colle_apres_la_reponse():
    """Observé le 28/07/2026 : le modèle rédige une phrase PUIS ajoute le JSON de
    l'appel. Ne regarder que le début de la réponse laissait passer ce cas."""
    melange = (
        "Pour décider, j'ai besoin des scores.\n\n"
        '[{"name": "predire_conformite", "arguments": {"indices": [13, 17]}}]'
    )
    assert agent._est_un_appel_en_texte(melange)


def test_chaque_question_repart_d_une_conversation_vierge():
    """Sur une question de suivi, le modèle réutilise le format de la réponse précédente
    et énonce un score sans appeler l'outil — 0,1248 annoncé là où l'endpoint renvoie
    0,4642. L'interface ne montrant pas de conversation, on la remet à zéro."""

    class AgentAvecHistorique:
        def __init__(self):
            self.messages = [{"role": "user"}, {"role": "assistant"}]
            self._qc_appels = []

        def __call__(self, question):
            self._qc_appels.append("predire_conformite")
            return "score 0.4642"

    objet = AgentAvecHistorique()
    agent.ask("La pièce 17 ?", agent=objet)

    assert objet.messages == []
