"""`qc chaos` casse une ressource du binôme, `qc heal` la répare en la nommant.

Deux invariants gardés ici :

  - `inject` ne touche qu'aux ressources du groupe, et seulement si elles sont en
    service — casser un endpoint absent n'aurait rien à diagnostiquer ;
  - `heal` ne redéploie JAMAIS un endpoint sans marqueur : un endpoint absent est
    aussi l'état normal après le teardown du soir, et le recréer d'office relancerait
    la facturation à l'insu du binôme.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from qc import chaos
from qc.config import config


@pytest.fixture
def marker(tmp_path, monkeypatch):
    fichier = tmp_path / ".qc-chaos.json"
    monkeypatch.setattr(chaos, "MARKER", fichier)
    return fichier


def _endpoint_in_service(aws):
    aws.repondre("sagemaker", "describe_endpoint", {"EndpointStatus": "InService"})


def _service_actif(aws, desired=1):
    aws.repondre(
        "ecs",
        "describe_services",
        {"services": [{"status": "ACTIVE", "desiredCount": desired}]},
    )


def _sample_absent(aws):
    aws.echouer("s3", "head_object", "404")


def _sample_present(aws):
    aws.repondre("s3", "head_object", {"ContentLength": 1})


# ---------------------------------------------------------------------------- inject


def test_inject_endpoint_supprime_le_seul_endpoint_du_groupe(aws, marker):
    _endpoint_in_service(aws)

    code = chaos.inject("endpoint")

    assert code == "endpoint"
    kwargs = aws.kwargs("sagemaker", "delete_endpoint")
    assert kwargs["EndpointName"] == config.endpoint_name
    assert marker.exists()


def test_inject_service_met_le_desired_count_a_zero(aws, marker):
    _service_actif(aws)

    chaos.inject("service")

    kwargs = aws.kwargs("ecs", "update_service")
    assert kwargs == {
        "cluster": config.ecs_cluster,
        "service": config.ecs_service,
        "desiredCount": 0,
    }


def test_inject_sample_supprime_la_bonne_cle(aws, marker):
    _sample_present(aws)

    chaos.inject("sample")

    kwargs = aws.kwargs("s3", "delete_object")
    assert kwargs["Key"] == "curated/sample.csv"
    assert kwargs["Bucket"] == config.s3_bucket


def test_inject_refuse_une_panne_dont_la_ressource_est_absente(aws, marker):
    aws.echouer("sagemaker", "describe_endpoint", "ValidationException")

    with pytest.raises(chaos.ChaosError, match="impossible"):
        chaos.inject("endpoint")
    assert not marker.exists()


def test_inject_sans_ressource_en_service_est_un_message_pas_une_trace(aws, marker):
    aws.echouer("sagemaker", "describe_endpoint", "ValidationException")
    _sample_absent(aws)
    aws.echouer("ecs", "describe_services", "ClusterNotFoundException")

    with pytest.raises(chaos.ChaosError, match="rien à casser"):
        chaos.inject()


def test_inject_au_hasard_ne_tire_que_parmi_le_disponible(aws, marker):
    # Seul le service est en service : le tirage n'a qu'un candidat possible.
    aws.echouer("sagemaker", "describe_endpoint", "ValidationException")
    _sample_absent(aws)
    _service_actif(aws)

    tirages = []

    def choisir(candidats):
        tirages.append([p.code for p in candidats])
        return candidats[0]

    code = chaos.inject(choisir=choisir)

    assert code == "service"
    assert tirages == [["service"]]


# ------------------------------------------------------------------------------ heal


def test_heal_relance_un_service_a_zero_meme_sans_marqueur(aws, marker):
    _endpoint_in_service(aws)
    _sample_present(aws)
    _service_actif(aws, desired=0)

    reparations = chaos.heal()

    assert aws.kwargs("ecs", "update_service")["desiredCount"] == 1
    assert any("service" in r for r in reparations)


def test_heal_sans_marqueur_ne_redeploie_pas_un_endpoint_absent(aws, marker):
    aws.echouer("sagemaker", "describe_endpoint", "ValidationException")
    _sample_present(aws)
    _service_actif(aws)

    reparations = chaos.heal()

    assert "create_model" not in aws.operations("sagemaker")
    assert any("teardown" in r for r in reparations)


def test_heal_efface_le_marqueur_et_est_relancable(aws, marker):
    _endpoint_in_service(aws)
    _sample_present(aws)
    _service_actif(aws, desired=0)
    marker.write_text('{"panne": "service", "quand": "2026-09-13T08:00:00Z"}\n')

    chaos.heal()
    assert not marker.exists()

    _service_actif(aws)
    assert chaos.heal() == []
