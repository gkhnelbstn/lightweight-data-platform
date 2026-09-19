"""Integration flows, one file per direction, and the pair two of them make.

The pair is hypothetical -- Siber (ERP) and Zirve (accounting) on SQL Server,
the case in issue #53, whose real schemas are not available. It is built to
have the real pair's problems: different column names, a classified
identifier on both sides, a column coded `'E'/'H'` on one side and as a bit
on the other, and both sides editing the same customer card. The table
contracts here know nothing about the integration; the flows carry all of it
(ADR 0019). Nothing lives in `contracts/` because nothing serves this pair.
"""
from __future__ import annotations

import copy

from core import flows


def _table(cid, name, columns):
    return {"id": cid, "schema": [{"name": name, "properties": columns}]}


SIBER = _table("siber.cari", "CARI", [
    {"name": "CARI_KOD", "primaryKey": True, "required": True},
    {"name": "UNVAN", "required": True},
    {"name": "VERGI_NO", "classification": "pii"},
    {"name": "AKTIF"},
    {"name": "BAKIYE"}])

ZIRVE = _table("zirve.hesap", "Hesap", [
    {"name": "HesapKodu", "primaryKey": True, "required": True},
    {"name": "Ad", "required": True},
    {"name": "VKN", "classification": "pii"},
    {"name": "Durum"},
    {"name": "Bakiye"},
    {"name": "KayitTarihi", "required": True}])

BY_ID = {c["id"]: c for c in (SIBER, ZIRVE)}

TO_ZIRVE = {
    "id": "siber_to_zirve", "from": "siber.cari", "to": "zirve.hesap",
    "columns": {"HesapKodu": "CARI_KOD", "Ad": "UNVAN", "VKN": "VERGI_NO",
                "Durum": "AKTIF", "Bakiye": "BAKIYE"},
    "values": {"Durum": {"E": True, "H": False}},
    "winsOnConflict": ["HesapKodu", "Ad", "VKN", "Durum"],
    "filledByTarget": ["KayitTarihi"]}

TO_SIBER = {
    "id": "zirve_to_siber", "from": "zirve.hesap", "to": "siber.cari",
    "columns": {"CARI_KOD": "HesapKodu", "UNVAN": "Ad", "VERGI_NO": "VKN",
                "AKTIF": "Durum", "BAKIYE": "Bakiye"},
    "values": {"AKTIF": {True: "E", False: "H"}},
    "winsOnConflict": ["BAKIYE"]}


def _check(*docs, by_id=BY_ID) -> list[str]:
    return flows.problems([flows.parse(d) for d in docs], by_id)


def _with(doc, **changes):
    out = copy.deepcopy(doc)
    out.update(changes)
    return out


# --- the vocabulary --------------------------------------------------------

def test_a_flow_file_reads_into_a_mapping():
    flow = flows.parse(TO_ZIRVE)
    assert (flow.mapping.reference, flow.target) == ("siber.cari", "zirve.hesap")
    assert flow.mapping.values == {"Durum": {"E": True, "H": False}}
    assert flow.wins == {"HesapKodu", "Ad", "VKN", "Durum"}


def test_the_table_contracts_say_nothing_about_the_integration():
    """The point of ADR 0019: a vendor's contract describes its table only."""
    for table in (SIBER, ZIRVE):
        assert "customProperties" not in table


def test_no_flows_directory_means_no_flows(tmp_path):
    assert flows.load(tmp_path / "missing") == []


def test_a_flow_file_on_disk(tmp_path):
    (tmp_path / "siber_to_zirve.yaml").write_text(
        "id: siber_to_zirve\nfrom: siber.cari\nto: zirve.hesap\n"
        "columns: {Ad: UNVAN}\nvalues: {Durum: {E: true, H: false}}\n",
        encoding="utf-8")
    [flow] = flows.load(tmp_path)
    assert flow.mapping.columns == {"Ad": "UNVAN"}
    assert flow.mapping.values == {"Durum": {"E": True, "H": False}}


# --- one direction ----------------------------------------------------------

def test_a_sound_pair_is_silent():
    assert _check(TO_ZIRVE, TO_SIBER) == []


def test_an_unresolvable_table_is_reported():
    assert _check(_with(TO_ZIRVE, to="zirve.yok")) == [
        "siber_to_zirve: names 'zirve.yok', which is not a contract this "
        "platform loads"]


def test_the_one_way_refusals_are_mappings():
    """Shared with derivedFrom: a column the target does not declare."""
    doc = copy.deepcopy(TO_ZIRVE)
    doc["columns"]["Adi"] = "UNVAN"
    assert any("fills 'Adi', which this contract does not declare" in p
               for p in _check(doc))


def test_a_classified_value_may_not_land_unclassified():
    zirve = copy.deepcopy(ZIRVE)
    zirve["schema"][0]["properties"][2].pop("classification")
    found = _check(TO_ZIRVE, by_id={**BY_ID, "zirve.hesap": zirve})
    assert any("may not lose its classification" in p for p in found)


def test_a_required_target_column_nothing_fills():
    found = _check(_with(TO_ZIRVE, filledByTarget=[]))
    assert found == ["siber_to_zirve: KayitTarihi is required here and no "
                     "mapping fills it; the contract promises a column "
                     "nothing puts a value in"]


def test_two_flows_into_one_column():
    other = {"id": "logo_to_zirve", "from": "siber.cari", "to": "zirve.hesap",
             "columns": {"Ad": "UNVAN"}}
    assert any("'Ad' is filled twice" in p for p in _check(TO_ZIRVE, other))


# --- the pair ----------------------------------------------------------------

def test_a_column_nothing_maps_back():
    doc = copy.deepcopy(TO_SIBER)
    del doc["columns"]["BAKIYE"]
    found = _check(TO_ZIRVE, doc)
    assert ("siber_to_zirve: 'Bakiye' is filled from 'BAKIYE' but "
            "zirve_to_siber does not map it back, so an edit made at "
            "zirve.hesap is overwritten by the next change at siber.cari"
            ) in found


def test_a_round_trip_into_another_column():
    doc = copy.deepcopy(TO_SIBER)
    doc["columns"]["BAKIYE"] = "Ad"
    found = _check(TO_ZIRVE, doc)
    assert ("siber_to_zirve: 'Bakiye' is filled from 'BAKIYE', but "
            "zirve_to_siber fills 'BAKIYE' from 'Ad'; the round trip moves "
            "the value into another column") in found


def test_a_column_neither_flow_wins():
    found = _check(_with(TO_ZIRVE, winsOnConflict=["HesapKodu", "Ad", "Durum"]),
                   TO_SIBER)
    assert found == ["siber_to_zirve: 'VKN' <-> 'VERGI_NO' is edited on both "
                     "sides and neither flow wins it, so a conflict is "
                     "settled by timing"]


def test_a_column_both_flows_win():
    found = _check(TO_ZIRVE, _with(TO_SIBER, winsOnConflict=["BAKIYE", "UNVAN"]))
    assert any("'Ad' <-> 'UNVAN' is won by both" in p for p in found)


def test_a_pair_problem_is_reported_once():
    """A fact about the pair, so `--check` must not count it twice."""
    assert len(_check(_with(TO_ZIRVE, winsOnConflict=["HesapKodu", "Ad",
                                                      "Durum"]), TO_SIBER)) == 1


def test_a_value_map_on_one_side_only():
    found = _check(TO_ZIRVE, _with(TO_SIBER, values={}))
    assert found == ["siber_to_zirve: 'Durum' <-> 'AKTIF' translates values "
                     "in one direction only (zirve_to_siber copies them), so "
                     "a round trip writes a translated value back "
                     "untranslated"]


def test_a_value_map_that_sends_two_values_to_one():
    doc = _with(TO_ZIRVE, values={"Durum": {"E": True, "H": False, "Y": True}})
    assert any("sends two values to one" in p for p in _check(doc, TO_SIBER))


def test_value_maps_that_are_not_inverses():
    doc = _with(TO_SIBER, values={"AKTIF": {True: "H", False: "E"}})
    assert _check(TO_ZIRVE, doc) == [
        "siber_to_zirve: the value map for 'Durum' is not the inverse of "
        "zirve_to_siber's for 'AKTIF', so a round trip changes the value"]
