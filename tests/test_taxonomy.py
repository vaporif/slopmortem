from __future__ import annotations

from pathlib import Path

import yaml

from slopmortem.models import Facets


def test_taxonomy_keys_match_facets_fields():
    tax = yaml.safe_load(Path("slopmortem/corpus/taxonomy.yml").read_text())
    closed_keys = {"sector", "business_model", "customer_type", "geography", "monetization"}
    assert set(tax.keys()) == closed_keys
    facets_fields = set(Facets.model_fields.keys())
    assert closed_keys <= facets_fields


def test_every_closed_enum_has_other():
    tax = yaml.safe_load(Path("slopmortem/corpus/taxonomy.yml").read_text())
    for key, values in tax.items():
        assert "other" in values, f"{key} missing 'other' fallback"
