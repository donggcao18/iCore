"""Pytest plugin loaded in historical benchmark interpreters (Python 3.6+)."""

import json
import os


_events = []
_collected = []


def pytest_collection_finish(session):
    _collected.extend(item.nodeid for item in session.items)


def pytest_collectreport(report):
    if report.failed:
        _events.append(dict(nodeid=report.nodeid, phase='collect', outcome='failed',
                            diagnostic=str(report.longrepr)))


def pytest_runtest_logreport(report):
    _events.append(dict(nodeid=report.nodeid, phase=report.when,
                        outcome=report.outcome, xfail=hasattr(report, 'wasxfail'),
                        diagnostic=str(report.longrepr) if report.longrepr else '',
                        stdout=getattr(report, 'capstdout', ''),
                        stderr=getattr(report, 'capstderr', '')))


def pytest_sessionfinish(session, exitstatus):
    destination = os.environ['ICORE_BRT_REPORT']
    with open(destination, 'w') as handle:
        json.dump(dict(collected=_collected, events=_events, exitcode=int(exitstatus)), handle)
