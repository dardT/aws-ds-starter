"""J2 · Application Streamlit — l'agent mis entre les mains d'un opérateur.

Elle ne contient aucune logique métier : tout est dans `qc.agent` et `qc.inference`, déjà
écrits et testés au J1. C'est le point de la journée — on met en service du code existant,
on ne le réécrit pas pour l'occasion.

Lancement local :

    uv run streamlit run app/main.py

Derrière l'ALB, le conteneur reçoit `GET /g01/` et doit donc être démarré avec
`--server.baseUrlPath=g01` (décision D12). Sans cela Streamlit se croit à la racine,
génère ses ressources statiques sur `/static/…` que l'ALB ne route pas, et l'utilisateur
obtient une page blanche devant une tâche parfaitement saine. Le `Dockerfile` s'en charge
à partir de `TEAM_ID`.
"""

from __future__ import annotations

import streamlit as st

from qc import agent, inference
from qc.config import DATA_DIR, config

st.set_page_config(page_title="Contrôle qualité", page_icon="🔧", layout="wide")


@st.cache_data
def _echantillon():
    """Charge l'échantillon, depuis le disque local ou depuis S3.

    Dans le conteneur, `data/` n'existe pas : `.dockerignore` l'exclut, et il est produit
    à l'exécution par `qc load`. L'application va donc chercher le fichier dans S3.

    Sans ce repli, l'application démarre, répond 200 sur son contrôle de santé, et
    s'arrête sur une erreur dès la première ligne — une tâche parfaitement saine devant
    une application inutilisable. C'est le même mode d'échec que le préfixe d'URL de D12,
    et il est tout aussi invisible depuis l'extérieur.
    """
    return inference.read_sample(inference.local_sample())


st.title("Contrôle qualité — assistance à la décision")
st.caption(f"groupe {config.team_id} · endpoint {config.endpoint_name} · {config.region}")

try:
    entete, lignes = _echantillon()
except inference.InferenceError as exc:
    st.error(str(exc))
    st.stop()

onglet_pieces, onglet_agent = st.tabs(["Pièces", "Assistant"])

# --------------------------------------------------------------------------- pièces

with onglet_pieces:
    st.subheader("Évaluer un lot")

    choix = st.multiselect(
        "Pièces à contrôler",
        options=list(range(len(lignes))),
        default=list(range(min(5, len(lignes)))),
    )

    # Le seuil est RÉGLABLE, et c'est délibéré. Le J1 laisse la question ouverte ; ici
    # l'opérateur voit le nombre de pièces écartées bouger quand il le déplace, ce qui
    # rend l'arbitrage concret au lieu de théorique. Sa position initiale est celle
    # choisie à l'entraînement (F2 sur le train, lue dans l'artefact) — pas un 0,50
    # implicite ; si l'artefact est injoignable, on retombe sur 0,50 sans bloquer l'app.
    try:
        seuil_entrainement = inference.decision_threshold()
    except Exception:  # noqa: BLE001
        seuil_entrainement = 0.5
    seuil = st.slider(
        "Seuil de décision",
        min_value=0.0,
        max_value=1.0,
        value=float(seuil_entrainement),
        step=0.01,
        help="Le modèle renvoie une probabilité. Le seuil relève du métier : écarter une "
        "bonne pièce et laisser passer une pièce défectueuse n'ont pas le même coût. "
        "La position initiale est le seuil choisi à l'entraînement (F2 sur le train).",
    )

    if st.button("Contrôler", type="primary", disabled=not choix):
        try:
            predictions = inference.predict([lignes[i] for i in choix])
        except inference.InferenceError as exc:
            st.error(str(exc))
        else:
            ecartees = [p for p in predictions if p.score >= seuil]

            colonne_a, colonne_b = st.columns(2)
            colonne_a.metric("Pièces contrôlées", len(predictions))
            colonne_b.metric("Pièces écartées", len(ecartees))

            st.dataframe(
                [
                    {
                        "pièce": choix[p.index],
                        "score": round(p.score, 4),
                        "décision": p.label(threshold=seuil),
                    }
                    for p in sorted(predictions, key=lambda p: -p.score)
                ],
                use_container_width=True,
                hide_index=True,
            )

# --------------------------------------------------------------------------- agent

with onglet_agent:
    st.subheader("Poser une question")
    st.caption(
        "L'assistant interroge lui-même le modèle de contrôle qualité. "
        "Les outils qu'il a appelés sont affichés sous sa réponse."
    )

    question = st.text_input(
        "Question",
        placeholder="La pièce 13 est-elle conforme ? Compare-la aux autres et dis-moi "
        "pourquoi elle obtient ce score.",
    )

    if st.button("Demander", disabled=not question):
        with st.spinner("L'assistant travaille…"):
            try:
                # Un agent NEUF à chaque question, sans mise en cache.
                #
                # Mesuré le 28/07/2026 : en réutilisant le même objet d'une question à
                # l'autre, le modèle finit par écrire ses appels d'outil en JSON au lieu
                # de les exécuter, ou par énoncer un score sans rien appeler — 0,1248
                # annoncé là où l'endpoint renvoie 0,4642. Avec un agent neuf, cinq
                # essais sur cinq donnent une réponse fondée.
                #
                # Vider `agent.messages` ne suffit pas : Strands conserve de l'état
                # ailleurs. La construction coûte environ une seconde, ce qui est le prix
                # d'une réponse qui ne raconte pas n'importe quoi.
                trace = agent.ask(question)
            except agent.AgentError as exc:
                st.error(str(exc))
            else:
                st.markdown(trace.reponse)

                # Afficher les outils appelés n'est pas un détail de mise au point : un
                # modèle qui répond sans avoir rien appelé produit un texte tout aussi
                # crédible. C'est ce que le J3 apprend à repérer.
                if trace.outils_appeles:
                    st.caption(f"Outils appelés : {', '.join(trace.outils_appeles)}")
                else:
                    st.warning(
                        "Aucun outil appelé — cette réponse ne s'appuie sur aucune "
                        "prédiction réelle."
                    )
