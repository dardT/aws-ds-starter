"""Tests de `qc.storage`.

Aucun appel AWS : ces tests portent sur les décisions de nommage et sur les messages
d'erreur, c'est-à-dire sur ce qui casse silencieusement. Le dépôt réel est vérifié par
`uv run qc upload` et par le contrôle n°4 du preflight, qui écrivent pour de vrai.
"""

from __future__ import annotations

import pytest

from qc import storage


def test_train_et_test_partent_dans_leur_propre_sous_prefixe():
    """SageMaker consomme un PRÉFIXE entier, pas un fichier.

    Si `train.csv` était déposé à plat dans `curated/`, le canal d'entrée avalerait
    aussi `sample.csv` et `timeline.csv`, et le job échouerait vingt minutes plus tard
    sur un nombre de colonnes inattendu.
    """
    assert storage._key("train.csv") == "curated/train/train.csv"
    assert storage._key("test.csv") == "curated/test/test.csv"


def test_les_fichiers_auxiliaires_restent_a_plat():
    """Ils ne sont jamais lus par un job : leur donner un sous-préfixe n'aurait
    d'autre effet que de laisser croire qu'ils en sont un canal d'entrée."""
    assert storage._key("sample.csv") == "curated/sample.csv"
    assert storage._key("timeline.csv") == "curated/timeline.csv"


def test_le_canal_d_entrainement_pointe_le_prefixe_et_non_le_fichier():
    report = storage.Report(bucket="qc-g01-data-<suffixe>", prefix="curated", uploads=())
    assert report.training_input == "s3://qc-g01-data-<suffixe>/curated/train/"
    assert report.training_input.endswith("/"), (
        "SageMaker exige un préfixe terminé par / ; sans lui il interprète la valeur "
        "comme une clé unique."
    )


def test_uri_d_un_fichier_et_du_prefixe():
    report = storage.Report(bucket="b", prefix="curated", uploads=())
    assert report.uri() == "s3://b/curated/"
    assert report.uri("sample.csv") == "s3://b/curated/sample.csv"


def test_upload_refuse_une_source_incomplete_avec_un_message_actionnable(tmp_path):
    """L'erreur la plus fréquente à cette étape n'est pas technique : c'est d'avoir
    sauté `qc load`. Le message doit le dire, pas afficher une trace boto3."""
    (tmp_path / "train.csv").write_text("0,1\n")

    with pytest.raises(storage.StorageError) as exc:
        storage.upload(tmp_path)

    message = str(exc.value)
    assert "test.csv" in message
    assert "qc load" in message, "le message doit nommer la commande corrective"


def test_la_liste_des_fichiers_attendus_couvre_les_trois_jours():
    """train/test pour l'entraînement, sample pour `invoke`, timeline pour la dérive
    temporelle du J3. Retirer l'un d'eux casse une journée entière, en silence."""
    assert set(storage.EXPECTED) == {
        "train.csv",
        "test.csv",
        "sample.csv",
        "timeline.csv",
    }


def test_le_total_agrege_les_tailles():
    report = storage.Report(
        bucket="b",
        prefix="curated",
        uploads=(
            storage.Upload(key="curated/a.csv", size=100, etag="x"),
            storage.Upload(key="curated/b.csv", size=250, etag="y"),
        ),
    )
    assert report.total_bytes == 350
    assert report.uploads[0].filename == "a.csv"


# ---------------------------------------------------------------------------------
# Appels AWS — vérifiés contre un client factice (voir tests/conftest.py).
# ---------------------------------------------------------------------------------


def rapport(taille: int = 100) -> storage.Report:
    return storage.Report(
        bucket="qc-g01-data-<suffixe>",
        prefix="curated",
        uploads=(storage.Upload(key="curated/train/train.csv", size=taille, etag="x"),),
    )


def test_verify_ne_signale_rien_quand_tout_est_conforme(aws):
    aws.repondre(
        "s3", "head_object", {"ContentLength": 100, "ServerSideEncryption": "AES256"}
    )
    assert storage.verify(rapport()) == []


def test_verify_detecte_un_objet_tronque(aws):
    """Un job d'entraînement lancé sur un objet incomplet échoue vingt minutes plus
    tard, sur un message qui ne parle pas du fichier."""
    aws.repondre(
        "s3", "head_object", {"ContentLength": 42, "ServerSideEncryption": "AES256"}
    )
    anomalies = storage.verify(rapport())

    assert len(anomalies) == 1
    assert "42" in anomalies[0] and "100" in anomalies[0]


def test_verify_detecte_un_chiffrement_absent(aws):
    aws.repondre("s3", "head_object", {"ContentLength": 100})
    assert "chiffrement" in storage.verify(rapport())[0]


def test_verify_signale_un_objet_disparu(aws):
    aws.echouer("s3", "head_object", "404", "Not Found")
    assert "introuvable" in storage.verify(rapport())[0]


def test_probe_isolation_reconnait_un_refus_comme_le_resultat_attendu(aws):
    """Un refus n'est pas une panne ici : c'est ce que le lab cherche à montrer.
    La fonction ne doit donc jamais lever."""
    aws.echouer("s3", "list_objects_v2", "AccessDenied", "refusé")
    message = storage.probe_isolation("g02")

    assert "attendu" in message
    assert "qc-g02-data-" in message


def test_probe_isolation_alerte_quand_la_lecture_reussit(aws):
    """État constaté aujourd'hui : seul le rôle d'exécution SageMaker est scopé, rien
    ne contraint encore l'identité de l'apprenant (décision D21). Le message doit dire
    quoi faire plutôt que de laisser croire à un succès."""
    aws.repondre("s3", "list_objects_v2", {"Contents": []})
    message = storage.probe_isolation("g02")

    assert "LECTURE RÉUSSIE" in message
    assert "formateur" in message
