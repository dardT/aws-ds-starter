"""Tests de `qc.monitoring`.

Aucun appel à S3, à MLflow ni à CloudWatch. Ce qui est vérifié ici, c'est le découpage du
format de capture — que rien ne documente — et la lecture du résultat d'Evidently, dont la
clé a changé entre la 0.4 et la 0.7.

La comparaison réelle est vérifiée par `make check-day3`.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from qc import monitoring

DEUX_PIECES = json.dumps(
    {
        "captureData": {
            "endpointInput": {"data": "1.0,2.0\n3.0,4.0"},
            "endpointOutput": {"data": "0.10\n0.90"},
        }
    }
)


def test_une_requete_multi_lignes_donne_plusieurs_predictions():
    """Une requête portant vingt pièces produit UNE ligne de fichier contenant vingt
    lignes CSV. Compter les lignes du fichier donnerait 1 prédiction au lieu de 20."""
    mesures, scores, requetes = monitoring._parse_capture(DEUX_PIECES)

    assert mesures == [[1.0, 2.0], [3.0, 4.0]]
    assert scores == pytest.approx([0.10, 0.90])
    assert requetes == 1


def test_l_entree_est_une_chaine_csv_et_non_un_objet():
    """C'est le piège du format : `endpointInput.data` n'est pas une liste de nombres,
    c'est le CSV envoyé, tel quel."""
    ligne = json.loads(DEUX_PIECES)
    assert isinstance(ligne["captureData"]["endpointInput"]["data"], str)


def test_une_requete_desalignee_est_ignoree():
    """Trois entrées et deux sorties : on ne saurait pas quel score va à quelle pièce.
    L'ignorer vaut mieux que produire une comparaison fausse."""
    bancale = json.dumps(
        {
            "captureData": {
                "endpointInput": {"data": "1,2\n3,4\n5,6"},
                "endpointOutput": {"data": "0.1\n0.2"},
            }
        }
    )
    assert monitoring._parse_capture(bancale) == ([], [], 0)


def test_les_lignes_illisibles_n_arretent_pas_la_lecture():
    """Un fichier de capture peut contenir une ligne tronquée si l'endpoint a été
    supprimé pendant une écriture. Elle ne doit pas faire perdre le reste."""
    contenu = "{ceci n'est pas du json\n" + DEUX_PIECES
    mesures, _, _ = monitoring._parse_capture(contenu)

    assert len(mesures) == 2


def test_le_score_est_lu_meme_separe_par_des_virgules():
    """Le conteneur XGBoost sépare par des retours à la ligne ou par des virgules selon
    le nombre de lignes envoyées — même piège qu'au J1."""
    une = json.dumps(
        {
            "captureData": {
                "endpointInput": {"data": "1.0,2.0"},
                "endpointOutput": {"data": "0.4242"},
            }
        }
    )
    _, scores, _ = monitoring._parse_capture(une)
    assert scores == pytest.approx([0.4242])


def test_les_inferences_anterieures_a_la_baseline_sont_ecartees():
    """Le fichier de capture des invocations de la baseline atterrit une à deux minutes
    APRÈS le dépôt de baseline.csv : filtrer par date de fichier les compte comme
    production. Constaté le 29/07/2026 : 392 lignes de baseline dans la production,
    p = 1,0000 partout. Le filtre porte donc sur l'horodatage de CHAQUE enregistrement."""
    from datetime import datetime, timezone

    def enregistrement(horodatage: str) -> str:
        return json.dumps(
            {
                "captureData": {
                    "endpointInput": {"data": "1.0,2.0"},
                    "endpointOutput": {"data": "0.5"},
                },
                "eventMetadata": {"inferenceTime": horodatage},
            }
        )

    contenu = (
        enregistrement("2026-07-29T12:00:00Z") + "\n" + enregistrement("2026-07-29T13:00:00Z")
    )
    seuil = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)

    mesures, _, requetes = monitoring._parse_capture(contenu, apres=seuil)

    assert len(mesures) == 1
    assert requetes == 1


def test_un_enregistrement_sans_horodatage_est_conserve():
    """Mieux vaut une ligne de trop qu'une production silencieusement ignorée : un
    enregistrement sans `inferenceTime` lisible passe le filtre."""
    from datetime import datetime, timezone

    seuil = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)
    mesures, _, _ = monitoring._parse_capture(DEUX_PIECES, apres=seuil)

    assert len(mesures) == 2


def test_la_lecture_du_resultat_utilise_metric_name():
    """En 0.7 la clé est `metric_name`. Les exemples en ligne lisent `metric_id`, hérité
    de la 0.4 : la lecture renvoie None partout et le rapport paraît vide (D18)."""
    resultat = {
        "metrics": [
            {"metric_name": "DriftedColumnsCount(drift_share=0.5)", "value": {"count": 3.0, "share": 0.75}},
            {"metric_name": "ValueDrift(column=score,method=K-S p_value,threshold=0.05)", "value": 0.001},
            {"metric_name": "ValueDrift(column=sensor_000,method=K-S p_value,threshold=0.05)", "value": 0.6},
        ]
    }
    drift = monitoring._lire_resultat(resultat, colonnes=4, rapport=monitoring.Path("/tmp/x.html"))

    assert drift.colonnes_derivees == 3
    assert drift.part == pytest.approx(0.75)
    assert drift.p_value_score == pytest.approx(0.001)
    assert drift.details["sensor_000"] == pytest.approx(0.6)


def test_une_derive_du_score_est_signalee():
    drift = monitoring.Drift(3, 4, 0.75, 0.001, monitoring.Path("/tmp/x.html"))

    assert drift.score_a_derive
    assert drift.jeu_a_derive
    assert "DÉRIVE" in drift.summary()


def test_un_jeu_stable_ne_declenche_rien():
    drift = monitoring.Drift(0, 41, 0.0, 0.8, monitoring.Path("/tmp/x.html"))

    assert not drift.score_a_derive
    assert not drift.jeu_a_derive
    assert "stable" in drift.summary()


def test_comparer_sans_colonnes_communes_est_une_erreur_explicite():
    """Evidently ne compare que les colonnes communes et ignore les autres en silence.
    Une comparaison portant sur zéro colonne renvoie « aucune dérive », ce qui ressemble
    à un succès."""
    reference = pd.DataFrame({"sensor_000": [1.0, 2.0], "score": [0.1, 0.2]})
    courant = pd.DataFrame({"0": [1.0, 2.0], "1": [3.0, 4.0]})

    with pytest.raises(monitoring.MonitoringError) as exc:
        monitoring.compare(reference, courant)

    assert "colonnes communes" in str(exc.value)


def test_la_reference_est_le_jeu_de_test_pas_l_entrainement():
    """Même régularisée, la logistique choisit ses coefficients pour le train, et son
    seuil F2 en dérive : une baseline construite dessus aurait une distribution de
    scores optimiste. La référence doit être ce que le modèle produisait sur des
    données jamais vues au moment de la validation."""
    source = monitoring.build_baseline.__doc__
    assert "entraînement" in source and "optimiste" in source


def test_les_seuils_sont_ceux_de_la_documentation_en_ligne():
    """Garder la valeur par défaut d'Evidently permet aux binômes de lire la
    documentation officielle sans se demander pourquoi les chiffres diffèrent."""
    assert monitoring.P_VALUE_SEUIL == 0.05


# --- Handshake websocket (revue du 29/07/2026, point 3) ----------------------------------
#
# Le serveur local canné joue le rôle de Streamlit : il répond le statut qu'on lui donne,
# quel que soit le contenu de la requête. Ce qui est testé, c'est que le handshake est
# bien FORMÉ (méthode, Upgrade, Origin) et que le code de statut remonte tel quel — 101
# comme 403 — au lieu d'être avalé par une exception.


def _serveur_canne(ligne_statut: bytes):
    """Ouvre un serveur TCP local qui répond `ligne_statut` à toute requête HTTP."""
    import socket
    import threading

    serveur = socket.create_server(("127.0.0.1", 0))
    requete_recue = bytearray()

    def repondre():
        connexion, _ = serveur.accept()
        with connexion:
            # Lire jusqu'à la fin des en-têtes : le handshake n'a pas de corps.
            while b"\r\n\r\n" not in requete_recue:
                bloc = connexion.recv(4096)
                if not bloc:
                    break
                requete_recue.extend(bloc)
            connexion.sendall(ligne_statut + b"\r\nContent-Length: 0\r\n\r\n")

    fil = threading.Thread(target=repondre, daemon=True)
    fil.start()
    return serveur, requete_recue


def test_le_handshake_websocket_porte_l_origin_d_un_navigateur():
    serveur, requete = _serveur_canne(b"HTTP/1.1 101 Switching Protocols")
    port = serveur.getsockname()[1]

    statut = monitoring.websocket_handshake_status(
        "127.0.0.1", "/g01/_stcore/stream", "http://alb.interne", port=port
    )

    assert statut == 101
    texte = bytes(requete).decode()
    assert texte.startswith("GET /g01/_stcore/stream")
    assert "Origin: http://alb.interne" in texte
    assert "Upgrade: websocket" in texte
    serveur.close()


def test_un_refus_d_origine_remonte_en_403_pas_en_exception():
    """C'est tout l'intérêt du contrôle : 403 est un DIAGNOSTIC (origine refusée,
    STREAMLIT_BROWSER_SERVER_ADDRESS absent), pas une panne du script."""
    serveur, _ = _serveur_canne(b"HTTP/1.1 403 Forbidden")
    port = serveur.getsockname()[1]

    statut = monitoring.websocket_handshake_status(
        "127.0.0.1", "/g01/_stcore/stream", "http://alb.interne", port=port
    )

    assert statut == 403
    serveur.close()


def test_un_serveur_injoignable_donne_zero():
    import socket

    condamne = socket.create_server(("127.0.0.1", 0))
    port = condamne.getsockname()[1]
    condamne.close()  # le port est libéré : la connexion sera refusée

    statut = monitoring.websocket_handshake_status(
        "127.0.0.1", "/g01/_stcore/stream", "http://alb.interne", port=port, timeout=2
    )

    assert statut == 0
