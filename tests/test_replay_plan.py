"""The plan declares how each record is found again on a rerun. The declaration is checked as data; the wording of steps is never scanned."""
import pytest

from conftest import filled
from oqa import analysis, workspace
from oqa.store import SchemaError


def planned(session, **fields):
    config = workspace.load("demo")
    data = filled(session.cases(), config)
    data["cases"][0].update(fields)
    return data, config


def refused(session, match, **fields):
    data, config = planned(session, **fields)
    with pytest.raises(SchemaError, match=match):
        analysis.validate(data, session.cases(), config)


def test_the_wording_of_steps_is_not_scanned(session):
    data, config = planned(session)
    data["cases"][0]["steps"][0].update(action="Open the request with id 308 on 2026-11-22", expected="The order shows SO0042; có sẵn AUTO_TEST_site")
    analysis.validate(data, session.cases(), config)


def test_a_case_declares_its_records_or_says_it_has_none(session):
    refused(session, "replay_records", replay_records=[])
    data, config = planned(session, replay_records=[], replay_records_none_reason="the case only reads the list that every database has")
    analysis.validate(data, session.cases(), config)
    refused(session, "replay_records", replay_records=[], replay_records_none_reason="none")


def test_each_record_says_how_it_is_found_again(session):
    refused(session, "how must be one of", replay_records=[{"record": "the order", "how": "by position", "detail": ""}])
    refused(session, "needs a record", replay_records=[{"record": "", "how": "run_key"}])
    refused(session, "run variable name", replay_records=[{"record": "the order", "how": "captured", "detail": "not a name!"}])
    data, config = planned(session, replay_records=[{"record": "the order", "how": "captured", "detail": "order_1"}])
    analysis.validate(data, session.cases(), config)


def test_an_existing_record_must_be_a_declared_reference(session):
    refused(session, "text of one of the case's refs", replay_records=[{"record": "the file", "how": "existing_ref", "detail": "something else"}])
    data, config = planned(session)
    refs = data["cases"][0]["refs"]
    if refs:
        data["cases"][0]["replay_records"] = [{"record": "the file", "how": "existing_ref", "detail": refs[0]["text"]}]
        analysis.validate(data, session.cases(), config)


def test_an_analysis_from_before_these_fields_stays_readable(session):
    data, config = planned(session)
    del data["cases"][0]["replay_records"], data["cases"][0]["replay_records_none_reason"]
    analysis.validate(data, session.cases(), config)
