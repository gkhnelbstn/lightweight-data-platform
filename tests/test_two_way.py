"""A two-way pair: two contracts whose mappings point at each other.

The pair is hypothetical -- Siber (ERP) and Zirve (accounting) on SQL Server,
the case in issue #53, whose real schemas are not available. It is built to
have the real pair's problems: different column names, a classified
identifier on both sides, and both sides editing the same customer card.
It lives here rather than in `contracts/` because nothing serves it, and a
contract there is windowed, scored and catalogued.
"""
from __future__ import annotations

import copy

from core import mapping, two_way


def _contract(cid, table, columns, other, mapped, mastered):
    return {"id": cid,
            "customProperties": [
                {"property": "derivedFrom",
                 "value": [{"contract": other, "columns": mapped}]},
                {"property": "masteredHere", "value": mastered}],
            "schema": [{"name": table, "properties": columns}]}


SIBER = _contract(
    "siber.cari", "CARI",
    [{"name": "CARI_KOD", "primaryKey": True, "required": True},
     {"name": "UNVAN", "required": True},
     {"name": "VERGI_NO", "classification": "pii"},
     {"name": "BAKIYE"}],
    "zirve.hesap",
    {"CARI_KOD": "HesapKodu", "UNVAN": "Ad", "VERGI_NO": "VKN",
     "BAKIYE": "Bakiye"},
    ["CARI_KOD", "UNVAN", "VERGI_NO"])

ZIRVE = _contract(
    "zirve.hesap", "Hesap",
    [{"name": "HesapKodu", "primaryKey": True, "required": True},
     {"name": "Ad", "required": True},
     {"name": "VKN", "classification": "pii"},
     {"name": "Bakiye"}],
    "siber.cari",
    {"HesapKodu": "CARI_KOD", "Ad": "UNVAN", "VKN": "VERGI_NO",
     "Bakiye": "BAKIYE"},
    ["Bakiye"])


def _all(*contracts) -> list[str]:
    by_id = {c["id"]: c for c in contracts}
    return [p for c in contracts
            for p in mapping.problems(c, by_id) + two_way.problems(c, by_id)]


def _edit(contract, **columns):
    out = copy.deepcopy(contract)
    out["customProperties"][0]["value"][0]["columns"] = columns
    return out


def _master(contract, mastered):
    out = copy.deepcopy(contract)
    out["customProperties"][1]["value"] = mastered
    return out


def test_a_sound_pair_is_silent():
    """Also proves the one-way refusals do not trip on a pair: each side has
    a single mapping, so nothing is 'filled twice'."""
    assert _all(SIBER, ZIRVE) == []


def test_a_one_way_mapping_gets_nothing_from_here():
    one_way = copy.deepcopy(ZIRVE)
    one_way["customProperties"][0]["value"] = ["siber.cari"]
    assert two_way.problems(SIBER, {"zirve.hesap": one_way}) == []


def test_a_column_nothing_maps_back():
    zirve = _edit(ZIRVE, HesapKodu="CARI_KOD", Ad="UNVAN", VKN="VERGI_NO")
    found = two_way.problems(SIBER, {"zirve.hesap": zirve})
    assert found == ["siber.cari: 'BAKIYE' is filled from zirve.hesap.Bakiye "
                     "but nothing maps it back, so an edit made here is "
                     "overwritten by the next change there"]


def test_a_round_trip_into_another_column():
    siber = _edit(SIBER, CARI_KOD="HesapKodu", UNVAN="Ad", VERGI_NO="VKN",
                  BAKIYE="Ad")
    found = two_way.problems(siber, {"zirve.hesap": ZIRVE})
    assert any("'BAKIYE'" in p and "maps back into 'UNVAN'" in p
               for p in found)


def test_a_column_nobody_masters():
    found = two_way.problems(_master(SIBER, ["CARI_KOD", "UNVAN"]),
                             {"zirve.hesap": ZIRVE})
    assert found == ["siber.cari: 'VERGI_NO' <-> zirve.hesap.VKN is edited "
                     "on both sides and masteredHere names neither, so a "
                     "conflict is settled by timing"]


def test_a_column_both_sides_master():
    zirve = _master(ZIRVE, ["Bakiye", "Ad"])
    found = two_way.problems(SIBER, {"zirve.hesap": zirve})
    assert any("'UNVAN'" in p and "mastered on both sides" in p
               for p in found)


def test_a_master_problem_is_reported_once():
    """It is a fact about the pair, so `--check` must not count it twice."""
    zirve = _master(ZIRVE, [])
    assert len(_all(SIBER, zirve)) == 1


def test_the_classification_still_has_to_match_both_ways():
    """The one-way refusal applies to each direction of a pair."""
    zirve = copy.deepcopy(ZIRVE)
    zirve["schema"][0]["properties"][2].pop("classification")
    found = _all(SIBER, zirve)
    assert any("may not lose its classification" in p for p in found)
