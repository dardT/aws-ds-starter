"""Tests de `qc.inference`.

Aucun endpoint n'est créé : un endpoint est facturé tant qu'il tourne, et un test qui en
laisse un derrière lui coûte de l'argent en silence. Ces tests portent sur le découpage
de la réponse, la lecture de l'échantillon et les invariants du déploiement.
Le déploiement réel est vérifié par `make check-day1`.
"""

from __future__ import annotations

import pytest

from conftest import CorpsFactice
from qc import inference
from qc.config import config


def test_la_reponse_multi_lignes_est_separee_par_des_retours_a_la_ligne():
    """Bug réel, constaté le 27/07/2026 à la première invocation.

    Le séparateur dépend du nombre de lignes envoyées, ce qui n'est documenté nulle
    part. Découper sur `,` seulement lève un ValueError sur une chaîne de vingt nombres
    collés, et le message ne dit pas pourquoi.
    """
    body = "0.0023\n0.0039\n0.9183"
    scores = inference._parse_scores(body)
    assert [p.score for p in scores] == pytest.approx([0.0023, 0.0039, 0.9183])
    assert [p.index for p in scores] == [0, 1, 2]


def test_la_reponse_d_une_seule_ligne_reste_lisible():
    assert [p.score for p in inference._parse_scores("0.0234")] == pytest.approx([0.0234])


def test_le_decoupage_accepte_aussi_les_virgules():
    """Certaines versions du conteneur séparent par des virgules. Accepter les deux
    coûte une ligne et évite une panne à la prochaine montée de version."""
    scores = inference._parse_scores("0.1,0.2,0.3")
    assert [p.score for p in scores] == pytest.approx([0.1, 0.2, 0.3])


def test_les_lignes_vides_sont_ignorees():
    """Le corps se termine par un retour à la ligne. Sans filtrage, la dernière
    prédiction est un float('') qui lève."""
    assert len(inference._parse_scores("0.1\n0.2\n")) == 2


def test_le_score_est_une_probabilite_et_le_seuil_est_obligatoire():
    """`label()` n'a plus de défaut à 0,50 : le seuil réel est un livrable de
    l'entraînement (F2, hors-fold), lu par `decision_threshold()`. Un défaut implicite
    ferait paraître neutre un choix qui ne l'est pas."""
    p = inference.Prediction(index=0, score=0.4642)
    assert p.label(threshold=0.58) == "conforme"
    assert p.label(threshold=0.4) == "NON CONFORME"
    with pytest.raises(TypeError):
        p.label()  # noqa: PLE — l'absence de défaut est le comportement testé


def test_read_sample_dit_quoi_faire_si_le_fichier_manque(tmp_path):
    with pytest.raises(inference.InferenceError) as exc:
        inference.read_sample(tmp_path / "sample.csv")
    assert "qc load" in str(exc.value)


def test_read_sample_retire_l_en_tete(tmp_path):
    """`sample.csv` a un en-tête pour être lisible à l'œil ; l'endpoint n'en veut pas."""
    path = tmp_path / "sample.csv"
    path.write_text("sensor_000,sensor_001\n1.5,2.5\n3.5,4.5\n")

    header, rows = inference.read_sample(path)

    assert header == ["sensor_000", "sensor_001"]
    assert rows == [[1.5, 2.5], [3.5, 4.5]]
    assert all(isinstance(v, float) for row in rows for v in row)


def test_la_capture_est_totale():
    """Le J3 compare une baseline à la production. Échantillonner priverait la
    comparaison de volume sur trois heures de lab."""
    assert inference.CAPTURE_PERCENTAGE == 100


def test_un_endpoint_se_resume_sans_ouvrir_la_console():
    endpoint = inference.Endpoint(
        name="qc-g01-endpoint",
        model_name="qc-g01-model-x",
        config_name="qc-g01-endpoint-config-x",
        artifact="s3://b/model/x/output/model.tar.gz",
        capture_uri="s3://b/capture/",
    )
    resume = endpoint.summary()
    for attendu in ("qc-g01-endpoint", "model.tar.gz", "capture/"):
        assert attendu in resume


# ---------------------------------------------------------------------------------
# Appels AWS — vérifiés contre un client factice (voir tests/conftest.py).
#
# Aucun endpoint n'est créé ici non plus. Ce qui est vérifié, c'est le contenu exact
# des trois appels de `deploy` : c'est là que la data capture peut disparaître sans
# que rien ne le signale avant le J3.
# ---------------------------------------------------------------------------------


ARTEFACT = "s3://qc-g01-data-<suffixe>/model/qc-g01-train-x/output/model.tar.gz"

JOB_TERMINE = {
    "TrainingJobSummaries": [{"TrainingJobName": "qc-g01-train-x"}],
}


def test_latest_artifact_ne_retient_que_les_jobs_reussis(aws):
    """On interroge SageMaker plutôt que de lister S3 : le préfixe `model/` contient
    aussi les artefacts des essais ratés, et rien dans un nom d'objet ne le dit."""
    aws.repondre("sagemaker", "list_training_jobs", JOB_TERMINE)
    aws.repondre(
        "sagemaker", "describe_training_job", {"ModelArtifacts": {"S3ModelArtifacts": ARTEFACT}}
    )
    assert inference.latest_artifact() == ARTEFACT
    assert aws.kwargs("sagemaker", "list_training_jobs")["StatusEquals"] == "Completed"


def test_latest_artifact_dit_quoi_faire_quand_rien_n_a_ete_entraine(aws):
    aws.repondre("sagemaker", "list_training_jobs", {"TrainingJobSummaries": []})
    with pytest.raises(inference.InferenceError) as exc:
        inference.latest_artifact()
    assert "qc train" in str(exc.value)


def test_latest_artifact_suit_le_next_token_d_une_page_vide(aws):
    """L'API applique NameContains APRÈS le découpage en pages : quand le job le plus
    récent du compte n'est pas du groupe (un `qc-g01-exo-train-…` du lab, par exemple),
    la première page revient vide AVEC un NextToken. Constaté le 06/08/2026 — sans
    pagination, `deploy` répondait « aucun job » alors que l'entraînement avait réussi."""
    aws.repondre(
        "sagemaker",
        "list_training_jobs",
        [
            {"TrainingJobSummaries": [], "NextToken": "page-suivante"},
            JOB_TERMINE,
        ],
    )
    aws.repondre(
        "sagemaker", "describe_training_job", {"ModelArtifacts": {"S3ModelArtifacts": ARTEFACT}}
    )
    assert inference.latest_artifact() == ARTEFACT
    # Le second appel doit bien porter le jeton de la première page.
    dernier_appel = [kw for nom, kw in aws.client("sagemaker").appels
                     if nom == "list_training_jobs"][-1]
    assert dernier_appel.get("NextToken") == "page-suivante"


def test_deploy_cree_les_trois_objets_dans_l_ordre(aws):
    """Un EndpointConfig référence un Model, un Endpoint référence une config :
    l'ordre n'est pas un style, c'est une dépendance."""
    inference.deploy(ARTEFACT)

    assert aws.operations("sagemaker") == [
        "create_model",
        "create_endpoint_config",
        "create_endpoint",
    ]


def test_deploy_active_la_data_capture_a_100_pour_cent_entree_et_sortie(aws):
    """LE test du J1. Une capture désactivée donne un endpoint parfaitement sain, et
    le J3 découvre deux jours plus tard qu'il n'a rien à comparer à la baseline.

    Capturer seulement l'entrée priverait le J3 de la distribution des scores, qui est
    la dérive la plus visible.
    """
    inference.deploy(ARTEFACT)
    capture = aws.kwargs("sagemaker", "create_endpoint_config")["DataCaptureConfig"]

    assert capture["EnableCapture"] is True
    assert capture["InitialSamplingPercentage"] == 100
    assert capture["DestinationS3Uri"] == f"s3://{config.s3_bucket}/capture/"
    assert {o["CaptureMode"] for o in capture["CaptureOptions"]} == {"Input", "Output"}


def test_deploy_sert_l_artefact_demande_avec_le_role_d_execution(aws):
    inference.deploy(ARTEFACT)
    conteneur = aws.kwargs("sagemaker", "create_model")["PrimaryContainer"]

    assert conteneur["ModelDataUrl"] == ARTEFACT
    assert "sagemaker-scikit-learn" in conteneur["Image"]
    assert aws.kwargs("sagemaker", "create_model")["ExecutionRoleArn"] == (
        config.sagemaker_role_arn
    )


def test_deploy_dit_au_conteneur_quel_script_servir(aws):
    """Contrairement à un algorithme intégré, le conteneur SKLearn ne sait pas servir
    tout seul : sans SAGEMAKER_PROGRAM et l'archive de code, l'endpoint démarre puis
    répond 500 à la première invocation — et le message ne nomme pas la cause."""
    from qc import training

    inference.deploy(ARTEFACT)
    environnement = aws.kwargs("sagemaker", "create_model")["PrimaryContainer"]["Environment"]

    assert environnement["SAGEMAKER_PROGRAM"] == training.SERVE_ENTRY_POINT
    assert environnement["SAGEMAKER_SUBMIT_DIRECTORY"].endswith(training.CODE_KEY)
    # L'archive doit avoir été déposée sur S3 — le conteneur la télécharge au démarrage.
    assert aws.kwargs("s3", "put_object")["Key"] == training.CODE_KEY


def test_le_nom_de_l_endpoint_est_fixe_le_reste_est_horodate(aws):
    """Le groupe de logs précréé par le socle porte ce nom, et le J2 comme le J3
    doivent retrouver l'endpoint sans le chercher."""
    endpoint = inference.deploy(ARTEFACT)

    assert endpoint.name == config.endpoint_name
    assert endpoint.model_name != config.resource("model")
    assert endpoint.config_name.startswith(config.resource("endpoint-config"))


def test_un_second_deploy_met_a_jour_au_lieu_d_echouer(aws):
    """Sans cela, un binôme qui redéploie après un réentraînement se heurte à un nom
    déjà pris, et n'a aucun moyen évident de s'en sortir."""
    aws.echouer(
        "sagemaker", "create_endpoint", "ValidationException", "Endpoint already exist"
    )
    inference.deploy(ARTEFACT)

    assert "update_endpoint" in aws.operations("sagemaker")


def test_un_refus_de_creation_reste_lisible(aws):
    aws.echouer("sagemaker", "create_model", "AccessDeniedException", "pas le droit")
    with pytest.raises(inference.InferenceError) as exc:
        inference.deploy(ARTEFACT)
    assert "AccessDeniedException" in str(exc.value)


def test_wait_refuse_de_faire_croire_qu_un_endpoint_en_echec_se_repare(aws):
    aws.repondre(
        "sagemaker",
        "describe_endpoint",
        {"EndpointStatus": "Failed", "FailureReason": "artefact illisible"},
    )
    with pytest.raises(inference.InferenceError) as exc:
        inference.wait(config.endpoint_name, poll_seconds=0)

    assert "artefact illisible" in str(exc.value)
    assert "teardown" in str(exc.value)


def test_wait_rend_la_main_des_que_l_endpoint_est_en_service(aws):
    aws.repondre("sagemaker", "describe_endpoint", {"EndpointStatus": "InService"})
    assert inference.wait(config.endpoint_name, poll_seconds=0) == "InService"


def test_predict_envoie_du_csv_sans_en_tete(aws):
    """Le conteneur attend exactement les colonnes vues à l'entraînement. Un en-tête
    ou une colonne cible oubliée produit un 400 qui ne dit pas laquelle."""
    aws.repondre(
        "sagemaker-runtime",
        "invoke_endpoint",
        {"Body": CorpsFactice("0.0023\n0.9183\n")},
    )
    predictions = inference.predict([[1.5, 2.5], [3.5, 4.5]])
    envoye = aws.kwargs("sagemaker-runtime", "invoke_endpoint")

    assert envoye["ContentType"] == "text/csv"
    assert envoye["EndpointName"] == config.endpoint_name
    assert envoye["Body"].splitlines() == ["1.5,2.5", "3.5,4.5"]
    assert [p.score for p in predictions] == pytest.approx([0.0023, 0.9183])


def test_predict_nomme_la_cause_la_plus_frequente_d_un_rejet(aws):
    aws.echouer("sagemaker-runtime", "invoke_endpoint", "ModelError", "unable to evaluate")
    with pytest.raises(inference.InferenceError) as exc:
        inference.predict([[1.0, 2.0]])
    assert "colonnes" in str(exc.value)


def test_predict_renvoie_vers_deploy_si_l_endpoint_n_existe_pas(aws):
    aws.echouer("sagemaker-runtime", "invoke_endpoint", "ValidationError", "not found")
    with pytest.raises(inference.InferenceError) as exc:
        inference.predict([[1.0, 2.0]])
    assert "qc deploy" in str(exc.value)


def test_teardown_supprime_l_endpoint_et_sa_config_mais_garde_le_modele(aws):
    """Le Model ne coûte rien, et le garder permet de redéployer le lendemain sans
    réentraîner. C'est ce que fait le J2."""
    aws.repondre(
        "sagemaker",
        "describe_endpoint",
        {"EndpointStatus": "InService", "EndpointConfigName": "qc-g01-endpoint-config-x"},
    )
    supprimes = inference.teardown()

    assert aws.operations("sagemaker") == [
        "describe_endpoint",
        "delete_endpoint",
        "delete_endpoint_config",
    ]
    assert "delete_model" not in aws.operations("sagemaker")
    assert len(supprimes) == 2


def test_teardown_est_sans_effet_quand_il_n_y_a_rien_a_eteindre(aws):
    """Elle est lancée chaque soir, souvent deux fois. Une erreur ici ferait douter
    d'une extinction pourtant réussie."""
    aws.echouer("sagemaker", "describe_endpoint", "ValidationException", "not found")
    assert inference.teardown() == []
    assert "delete_endpoint" not in aws.operations("sagemaker")
