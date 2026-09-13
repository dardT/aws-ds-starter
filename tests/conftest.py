"""Outillage commun aux tests : un client boto3 factice.

Les fonctions qui appellent AWS — `submit`, `deploy`, `predict`, `teardown` — ne peuvent
pas être testées contre le vrai compte : un endpoint est facturé tant qu'il tourne, et un
test qui en laisse un derrière lui coûte de l'argent en silence.

Elles ne peuvent pas non plus rester sans test. Ce sont des constructions de dictionnaires,
et une erreur dedans (un canal mal nommé, une data capture désactivée) ne se voit qu'à
l'exécution réelle, deux jours plus tard.

D'où ce client factice : il enregistre les appels au lieu de les envoyer, et les tests
vérifient le contenu exact de ce qui serait parti sur le réseau.

    def test_quelque_chose(aws):
        aws.repondre("sagemaker", "describe_endpoint", {"EndpointStatus": "InService"})
        inference.deploy(artefact)
        assert aws.kwargs("sagemaker", "create_model")["ModelName"].startswith("qc-")
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from qc.config import config


class ClientFactice:
    """Remplace un client boto3. Toute méthode appelée est enregistrée."""

    def __init__(self) -> None:
        self.appels: list[tuple[str, dict]] = []
        self.reponses: dict[str, object] = {}
        self.erreurs: dict[str, Exception] = {}

    def __getattr__(self, operation: str):
        def appeler(**kwargs):
            self.appels.append((operation, kwargs))
            if operation in self.erreurs:
                raise self.erreurs[operation]
            reponse = self.reponses.get(operation, {})
            # Une liste sert à simuler une suite d'états : le sondage d'un job renvoie
            # « InProgress » puis « Completed ». La dernière valeur se répète ensuite.
            if isinstance(reponse, list):
                return reponse.pop(0) if len(reponse) > 1 else reponse[0]
            return reponse

        return appeler


class AWSFactice:
    """Fabrique de clients factices, un par service, avec ses réponses."""

    def __init__(self) -> None:
        self._clients: dict[str, ClientFactice] = {}

    def client(self, service: str) -> ClientFactice:
        return self._clients.setdefault(service, ClientFactice())

    # --- préparation ----------------------------------------------------------------

    def repondre(self, service: str, operation: str, reponse) -> "AWSFactice":
        self.client(service).reponses[operation] = reponse
        return self

    def echouer(self, service: str, operation: str, code: str, message: str = "") -> "AWSFactice":
        self.client(service).erreurs[operation] = ClientError(
            {"Error": {"Code": code, "Message": message}}, operation
        )
        return self

    # --- vérification ---------------------------------------------------------------

    def operations(self, service: str) -> list[str]:
        return [nom for nom, _ in self.client(service).appels]

    def kwargs(self, service: str, operation: str) -> dict:
        for nom, kw in self.client(service).appels:
            if nom == operation:
                return kw
        raise AssertionError(
            f"{operation} n'a jamais été appelée sur {service}."
            f" Appels reçus : {self.operations(service)}"
        )


class CorpsFactice:
    """Le corps d'une réponse `invoke_endpoint` : un flux à lire une fois."""

    def __init__(self, contenu: str) -> None:
        self._contenu = contenu.encode("utf-8")

    def read(self) -> bytes:
        return self._contenu


#: Identifiant de compte factice. Sa forme importe : `account_suffix` en prend les six
#: derniers caractères pour composer le nom de bucket.
COMPTE_FACTICE = "123456789012"


@pytest.fixture
def aws(monkeypatch) -> AWSFactice:
    """Remplace `config.client` pour toute la durée du test.

    Tous les modules partagent le même objet `config`, donc un seul remplacement suffit.

    `account_id` demande un remplacement SÉPARÉ : il ouvre son propre client STS par
    `boto3.Session`, sans passer par `config.client`. Sans ce remplacement, tout test
    dont le chemin touche `s3_bucket` appelle réellement `GetCallerIdentity` — il passe
    tant que les identifiants sont valides, et casse sur `ExpiredToken` dès qu'ils
    expirent, dans une suite censée ne jamais joindre AWS.
    """
    faux = AWSFactice()
    monkeypatch.setattr(config, "client", faux.client)

    # `cached_property` range sa valeur dans le `__dict__` de l'instance : l'y écrire
    # revient à la mettre en cache. On passe par le dictionnaire plutôt que par
    # `monkeypatch.setattr`, qui lit l'ancienne valeur avant d'écrire — et cette lecture
    # déclencherait justement l'appel STS qu'on cherche à éviter.
    #
    # `config` est un singleton partagé par toute la session de test : on vide ce qui
    # aurait été mis en cache avant, et on nettoie derrière soi.
    caches = ("account_id", "account_suffix", "s3_bucket")
    for cache in caches:
        config.__dict__.pop(cache, None)
    config.__dict__["account_id"] = COMPTE_FACTICE

    yield faux

    for cache in caches:
        config.__dict__.pop(cache, None)
