# Copyright 2024 Marcus Boden
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


import ops
import ops.testing
import pytest
from charm import PrometheusNumaExporterCharm

def read_nova_patch(_, search_str):
    if search_str == "passthrough_whitelist":
        return '[{ "devname": "enp5s0", "physical_network": "sriovfabric1", "trusted": "true"}]'
    if search_str == "cpu_dedicated_set":
        return "3-5"
def read_no_nova(_, search_str):
    raise OSError("/etc/nova/nova.conf not found or sth. What do I know...?")
def no_nova(_):
    raise OSError("/etc/nova/nova.conf not found or sth. What do I know...?")

@pytest.fixture
def harness(monkeypatch):
    # Instantiate the Ops library's test harness
    harness = ops.testing.Harness(PrometheusNumaExporterCharm)
    # Set a name for the testing model created by Harness (optional).
    # Cannot be called after harness.begin()
    harness.set_model_name("testing")
    harness.add_relation("juju-info", "ubuntu")
    harness.set_leader(True)
    # Instantiate an instance of the charm (harness.charm)
    monkeypatch.setattr(PrometheusNumaExporterCharm, "_run_cmd", lambda x,y: True)
    monkeypatch.setattr(PrometheusNumaExporterCharm, "_get_val_from_nova", read_nova_patch)

    harness.begin()
    yield harness
    # Run Harness' cleanup method on teardown
    harness.cleanup()

def test_install(harness: ops.testing.Harness[PrometheusNumaExporterCharm]):
    # Test initialisation of shared state in the charm
    assert harness.charm._snap_name == "prometheus-numa-exporter"
    assert harness.charm.config.get("channel") == "stable"
    assert isinstance(harness.model.unit.status, ops.MaintenanceStatus)

def test_config_changed(harness: ops.testing.Harness[PrometheusNumaExporterCharm]):
    # Simulates the update of config, triggers a config-changed event
    harness.update_config({"address": "0.0.0.0",  "port": 8989, "log-level": "error", "channel": "edge"})

    # Test the config-changed method stored the update in state
    assert harness.charm.config.get("address") == "0.0.0.0"
    assert harness.charm.config.get("port") == 8989
    assert harness.charm.config.get("log-level") == "error"
    assert harness.charm.config.get("channel") == "edge"
    assert isinstance(harness.model.unit.status, ops.ActiveStatus)

def test_wrong_config(harness: ops.testing.Harness[PrometheusNumaExporterCharm]):
    harness.update_config({"address": "5"})
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    harness.update_config({"address": "127.0.0.1", "port":-4})
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    harness.update_config({"port": 8989, "log-level":"Extra Verbose"})
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    harness.update_config({"log-level": "info", "channel":"bleeding edge"})
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    harness.update_config({"channel":"edge"})
    assert isinstance(harness.model.unit.status, ops.ActiveStatus)

def test_no_nova(monkeypatch, harness: ops.testing.Harness[PrometheusNumaExporterCharm]):
    monkeypatch.setattr(PrometheusNumaExporterCharm, "_get_val_from_nova", read_no_nova)
    with pytest.raises(OSError) as excinfo:
        harness.update_config()
    assert excinfo.type is OSError
