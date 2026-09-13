"""Tests de scripts/lint_iam.py — revue du 29/07/2026, point 8."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def lint_iam():
    spec = importlib.util.spec_from_file_location(
        "lint_iam", RACINE / "scripts" / "lint_iam.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_iam"] = module
    spec.loader.exec_module(module)
    return module


def test_un_joker_sans_justification_est_une_violation(lint_iam, tmp_path):
    (tmp_path / "mauvais.tf").write_text(
        'statement {\n  actions   = ["s3:GetObject"]\n  resources = ["*"]\n}\n',
        encoding="utf-8",
    )

    fautes = lint_iam.violations(tmp_path)

    assert len(fautes) == 1 and fautes[0].endswith("mauvais.tf:3")


def test_un_joker_justifie_sur_sa_ligne_passe(lint_iam, tmp_path):
    (tmp_path / "bon.tf").write_text(
        'statement {\n'
        '  actions   = ["cloudwatch:GetMetricData"]\n'
        '  resources = ["*"] // GetMetricData n\'accepte pas de restriction par ressource\n'
        "}\n",
        encoding="utf-8",
    )

    assert lint_iam.violations(tmp_path) == []


def test_une_ressource_precise_n_est_pas_concernee(lint_iam, tmp_path):
    (tmp_path / "scope.tf").write_text(
        'resources = ["arn:aws:s3:::qc-g01-data/*"]\n', encoding="utf-8"
    )

    assert lint_iam.violations(tmp_path) == []


def test_le_depot_lui_meme_est_propre(lint_iam):
    """Le lint fait partie de make ci : le dépôt doit le passer en permanence."""
    assert lint_iam.violations() == []
