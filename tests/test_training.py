"""Tests de `qc.training`.

Aucun job n'est soumis : un entraînement réel coûte plusieurs minutes de machine et ne
serait pas rejouable en intégration continue. Ces tests portent sur ce qui casse
silencieusement — l'URI de l'image, le contrat du conteneur (script + archive de code),
la lecture du résultat. Le job réel est vérifié par `uv run qc train`.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from qc import training
from qc.config import config


def test_l_uri_de_l_image_utilise_le_bon_compte_pour_eu_west_3(monkeypatch):
    """749696950732 est le compte des algorithmes intégrés qu'on trouve partout en
    ligne pour eu-west-3. Il est MORT : tout `docker pull` y échoue sur AccessDenied,
    vérifié le 29/07/2026. Le compte des conteneurs framework (SKLearn, XGBoost) est
    659782779980 — vérifié par un pull réel le même jour."""
    monkeypatch.setattr(training.config, "region", "eu-west-3", raising=False)
    uri = training.image_uri()
    assert uri.startswith("659782779980.dkr.ecr.eu-west-3.amazonaws.com/")
    assert "sagemaker-scikit-learn" in uri
    assert "749696950732" not in uri


def test_une_region_inconnue_donne_un_message_actionnable(monkeypatch):
    monkeypatch.setattr(training.config, "region", "ap-south-2", raising=False)
    with pytest.raises(training.TrainingError) as exc:
        training.image_uri()
    assert "SKLEARN_REGISTRY" in str(exc.value)


def test_l_archive_de_code_contient_les_trois_fichiers():
    """Le conteneur ne contient pas ce dépôt : tout ce dont le script a besoin doit
    voyager dans l'archive. Un import absent ne se voit qu'après trois minutes de job."""
    archive = tarfile.open(fileobj=io.BytesIO(training._sourcedir_bytes()))
    noms = sorted(m.name for m in archive.getmembers())
    assert noms == ["secom_model.py", "serve_entry.py", "train_entry.py"]


def test_le_module_embarque_est_bien_la_copie_de_qc_optimized():
    """`secom_model.py` doit rester la copie conforme de `qc_optimized/secom.py` : c'est
    ce qui garantit que le job SageMaker entraîne EXACTEMENT le modèle validé par le
    notebook, pas une variante recopiée à la main qui divergerait en silence."""
    import importlib.resources

    archive = tarfile.open(fileobj=io.BytesIO(training._sourcedir_bytes()))
    embarque = archive.extractfile("secom_model.py").read()
    source = importlib.resources.files("qc_optimized").joinpath("secom.py").read_bytes()
    assert embarque == source


def test_le_script_d_entrainement_choisit_son_seuil_hors_fold():
    """Le seuil F2 doit venir de prédictions hors-fold du train — jamais du canal
    validation, réservé à l'évaluation finale. On vérifie le mécanisme, pas le
    résultat : cross_val_predict présent, et aucun fit avant la sélection du seuil."""
    source = (training._MODEL_ASSETS / training.TRAIN_ENTRY_POINT).read_text()
    assert "cross_val_predict" in source
    assert source.index("select_f2_threshold") < source.index("model.fit")


def test_le_garde_fou_de_duree_est_serre():
    """La limite AWS par défaut est de 24 heures. Sur un compte partagé par six
    binômes, un job parti en vrille doit être tué en minutes, pas en jours."""
    assert training.MAX_RUNTIME_SECONDS <= 1800


def test_un_job_echoue_est_lisible_sans_avoir_a_ouvrir_la_console():
    result = training.Result(
        name="qc-g01-train-x",
        status="Failed",
        seconds=42,
        model_artifact="",
        metrics={},
        failure="ClientError: bucket introuvable",
    )
    assert not result.ok
    assert "bucket introuvable" in result.summary()


def test_un_job_reussi_affiche_ses_metriques_triees():
    result = training.Result(
        name="qc-g01-train-x",
        status="Completed",
        seconds=135,
        model_artifact="s3://b/model/x/output/model.tar.gz",
        metrics={
            "validation:roc_auc": 0.7859,
            "validation:average_precision": 0.2364,
        },
    )
    assert result.ok
    summary = result.summary()
    assert "2 min 15 s" in summary
    assert summary.index("average_precision") < summary.index("roc_auc")


# ---------------------------------------------------------------------------------
# Appels AWS — vérifiés contre un client factice (voir tests/conftest.py).
#
# Ces fonctions construisent des dictionnaires que rien ne valide avant l'appel réel.
# Un canal mal nommé ou un rôle oublié ne se voit qu'au bout de trois minutes de job.
# ---------------------------------------------------------------------------------


REPONSE_JOB_TERMINE = {
    "TrainingJobStatus": "Completed",
    "SecondaryStatus": "Completed",
    "TrainingTimeInSeconds": 135,
    "ModelArtifacts": {"S3ModelArtifacts": "s3://b/model/x/output/model.tar.gz"},
    "FinalMetricDataList": [
        {"MetricName": "validation:average_precision", "Value": 0.2364},
        {"MetricName": "validation:roc_auc", "Value": 0.7859},
    ],
}


def test_submit_declare_bien_deux_canaux(aws):
    """Sans canal `validation`, le job réussit mais ne produit AUCUNE métrique
    d'évaluation. Le problème n'apparaît qu'au J3, quand la baseline de dérive n'a
    plus rien à quoi se comparer."""
    training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    canaux = aws.kwargs("sagemaker", "create_training_job")["InputDataConfig"]
    assert [c["ChannelName"] for c in canaux] == ["train", "validation"]


def test_submit_consomme_des_prefixes_et_non_des_fichiers(aws):
    """SageMaker lit TOUT ce qui se trouve sous l'URI. Pointer un fichier précis
    n'est pas possible, d'où les sous-préfixes créés par storage.py."""
    training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    for canal in aws.kwargs("sagemaker", "create_training_job")["InputDataConfig"]:
        source = canal["DataSource"]["S3DataSource"]
        assert source["S3DataType"] == "S3Prefix"
        assert source["S3Uri"].endswith("/")


def test_submit_depose_le_code_avant_de_soumettre(aws):
    """L'archive de code doit être sur S3 AVANT que le job ne démarre : le conteneur la
    télécharge au démarrage, et une archive absente donne un job qui échoue après trois
    minutes de provisionnement."""
    training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    depot = aws.kwargs("s3", "put_object")
    assert depot["Key"] == training.CODE_KEY
    assert depot["ServerSideEncryption"] == "AES256"
    assert len(depot["Body"]) > 0


def test_submit_declare_le_contrat_du_conteneur_en_json(aws):
    """Les deux clés `sagemaker_*` disent au conteneur quel script lancer et où trouver
    son code. Elles doivent être JSON-encodées (avec guillemets) : c'est ainsi que le
    toolkit du conteneur les lit — une valeur nue est ignorée en silence."""
    training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    hyper = aws.kwargs("sagemaker", "create_training_job")["HyperParameters"]
    assert json.loads(hyper["sagemaker_program"]) == training.TRAIN_ENTRY_POINT
    assert json.loads(hyper["sagemaker_submit_directory"]).startswith("s3://")
    assert json.loads(hyper["sagemaker_submit_directory"]).endswith(training.CODE_KEY)


def test_submit_declare_les_metriques_a_extraire_des_logs(aws):
    """Le conteneur SKLearn ne publie rien nativement : sans MetricDefinitions, le job
    réussit mais `Result.metrics` reste vide, et le tableau de bord n'affiche rien."""
    training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    spec = aws.kwargs("sagemaker", "create_training_job")["AlgorithmSpecification"]
    noms = {m["Name"] for m in spec["MetricDefinitions"]}
    assert "validation:average_precision" in noms
    assert "validation:recall" in noms


def test_submit_passe_le_role_d_execution_et_non_les_credentials(aws, monkeypatch):
    """C'est SageMaker qui endosse ce rôle pour lire les données. Les droits de
    l'apprenant n'entrent jamais en jeu pendant l'entraînement.

    L'ARN est posé par le test : sur un poste il viendrait du `.env`, mais la CI n'a
    pas de SAGEMAKER_ROLE_ARN — le test doit être autonome vis-à-vis de l'environnement."""
    monkeypatch.setattr(
        config, "sagemaker_role_arn",
        "arn:aws:iam::123456789012:role/qc-g01-sagemaker-exec",
    )
    training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    envoye = aws.kwargs("sagemaker", "create_training_job")
    assert envoye["RoleArn"] == config.sagemaker_role_arn
    assert "sagemaker-exec" in envoye["RoleArn"]


def test_submit_borne_la_duree_et_horodate_le_nom(aws):
    """AWS refuse de réutiliser le nom d'un job, même supprimé : un nom fixe ferait
    échouer la deuxième exécution du binôme."""
    premier = training.submit("s3://b/curated/train/", "s3://b/curated/test/")
    envoye = aws.kwargs("sagemaker", "create_training_job")

    assert envoye["StoppingCondition"]["MaxRuntimeInSeconds"] == training.MAX_RUNTIME_SECONDS
    assert premier.name.startswith(config.resource("train"))
    assert premier.name != config.resource("train")
    assert envoye["OutputDataConfig"]["S3OutputPath"].endswith(f"/{training.MODEL_PREFIX}/")
    assert envoye["Tags"] == config.tags_list


def test_submit_traduit_un_refus_de_role_en_message_actionnable(aws):
    aws.echouer(
        "sagemaker",
        "create_training_job",
        "ValidationException",
        "Could not assume the execution role provided",
    )
    with pytest.raises(training.TrainingError) as exc:
        training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    assert "sagemaker.amazonaws.com" in str(exc.value)
    assert "n°6" in str(exc.value)


def test_submit_traduit_un_quota_atteint(aws):
    """Six binômes qui lancent en même temps saturent le quota par défaut. Le message
    doit nommer le quota, pas renvoyer le code d'erreur brut."""
    aws.echouer("sagemaker", "create_training_job", "ResourceLimitExceeded", "limit")
    with pytest.raises(training.TrainingError) as exc:
        training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    assert training.INSTANCE_TYPE in str(exc.value)
    assert "Quota" in str(exc.value)


def test_submit_traduit_un_passrole_refuse(aws):
    aws.echouer("sagemaker", "create_training_job", "AccessDeniedException", "denied")
    with pytest.raises(training.TrainingError) as exc:
        training.submit("s3://b/curated/train/", "s3://b/curated/test/")

    assert "iam:PassRole" in str(exc.value)


def test_wait_sonde_jusqu_a_la_fin_et_rend_compte_de_la_progression(aws):
    """`on_tick` est ce qui permet à cli.py d'afficher l'avancement sans que le module
    n'imprime quoi que ce soit lui-même."""
    aws.repondre(
        "sagemaker",
        "describe_training_job",
        [
            {"TrainingJobStatus": "InProgress", "SecondaryStatus": "Starting"},
            {"TrainingJobStatus": "InProgress", "SecondaryStatus": "Downloading"},
            REPONSE_JOB_TERMINE,
        ],
    )
    vus: list[str] = []
    result = training.wait("qc-g01-train-x", poll_seconds=0, on_tick=lambda s, _: vus.append(s))

    assert vus == ["Starting", "Downloading"]
    assert result.ok
    assert result.metrics["validation:average_precision"] == pytest.approx(0.2364)
    assert result.model_artifact.endswith("model.tar.gz")


def test_wait_remonte_la_raison_d_un_echec(aws):
    """Le message d'AWS nomme généralement la vraie cause — souvent un artefact que le
    rôle ne peut pas lire. Le perdre obligerait à ouvrir la console."""
    aws.repondre(
        "sagemaker",
        "describe_training_job",
        {"TrainingJobStatus": "Failed", "FailureReason": "AccessDenied sur curated/"},
    )
    result = training.wait("qc-g01-train-x", poll_seconds=0)

    assert not result.ok
    assert "AccessDenied" in result.summary()
