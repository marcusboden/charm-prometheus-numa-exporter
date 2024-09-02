#!/usr/bin/env python3
# Copyright 2024 Marcus Boden
# See LICENSE file for licensing details.

#!/usr/bin/env python3
# Copyright 2023 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""
import json
import logging
import subprocess
import ipaddress

import ops

from interface_prometheus.operator import (
    PrometheusConfigError,
    PrometheusConnected,
    PrometheusScrapeTarget,
)

# import requests_unixsocket

logger = logging.getLogger(__name__)


class PrometheusNumaExporterCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.prometheus_target = PrometheusScrapeTarget(self, "prometheus-scrape")
        self.framework.observe(
            self.prometheus_target.on.prometheus_available, self._on_prometheus_available
        )
        self._snap_name = "prometheus-numa-exporter"
        self._nova_conf = "/etc/nova/nova.conf"

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self.unit.status = ops.ActiveStatus()

    def _on_install(self, event: ops.InstallEvent):
        """Handle install event."""
        self.unit.status = ops.MaintenanceStatus("Installing prometheus-numa-exporter snap")
        channel = self.config.get("channel")
        if channel not in ["beta", "edge", "candidate", "stable"]:
            self.unit.status = ops.BlockedStatus("Invalid channel configured.")
            event.defer()
            return

        if not self._run_cmd(["snap", "install", self._snap_name, f"--{channel}"]):
            self.unit.status = ops.BlockedStatus("could not install snap")
            event.defer()
            return

        if not self._run_cmd(["snap", "connect", f"{self._snap_name}:libvirt"]):
            self.unit.status = ops.BlockedStatus("Could not connect snap to libvirt")
            event.defer()
            return

        if not self._run_cmd(["snap", "connect", f"{self._snap_name}:hardware-observe"]):
            self.unit.status = ops.BlockedStatus("Could not connect snap to hardware-observer")
            event.defer()
            return

        self.unit.status = ops.ActiveStatus("Ready")

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        channel = self.config.get("channel")
        if channel in ["beta", "edge", "candidate", "stable"]:
            self._run_cmd(["snap", "refresh", self._snap_name, f"--{channel}"])
            # workload_version = self._getWorkloadVersion()
            # self.unit.set_workload_version(workload_version)
        else:
            self.unit.status = ops.BlockedStatus(f"Invalid channel configured: {channel}")
            event.defer()
            return

        address = self.config.get("address")
        if address:
            try:
                ipaddress.ip_address(address)
            except ValueError:
                self.unit.status = ops.BlockedStatus(f"Invalid address configured: {address}")
                event.defer()
                return
            self._run_cmd(["snap", "set", self._snap_name, f"address={address}"])

        port = self.config.get("scrape-port")
        if port:
            if 1 <= port <= 65535:
                self._run_cmd(["snap", "set", self._snap_name, f"port={port}"])
            else:
                ops.BlockedStatus(f"Port {port} not between 1 and 65535")
                event.defer()
                return

        log_level = self.config.get("log-level")
        if log_level:
            if log_level in ["debug", "info", "warning", "error", "critical"]:
                self._run_cmd(["snap", "set", self._snap_name, f"level={log_level}"])
            else:
                ops.BlockedStatus(f"Invalid log-level configured: {log_level}")
                event.defer()
                return

        cds = self._get_val_from_nova("cpu_dedicated_set")
        if cds:
            self._run_cmd(["snap", "set", self._snap_name, f"cpu-dedicated-set={cds}"])

        nics = self._get_nics()
        if nics:
            self._run_cmd(["snap", "set", self._snap_name, f"network-interfaces={nics}"])

        self.reconfigure_scrape_target()
        self.unit.status = ops.ActiveStatus(f"Ready at {channel}")

    def reconfigure_scrape_target(self) -> None:
        """Update scrape target configuration in related Prometheus application.

        Note: this function has no effect if there's no application related via
        'prometheus-scrape'.
        """
        port = self.config["scrape-port"]
        interval_minutes = self.config["scrape-interval"]
        interval = interval_minutes * 60
        timeout = self.config["scrape-timeout"]
        try:
            self.prometheus_target.expose_scrape_target(
                port, "/metrics", scrape_interval=f"{interval}s", scrape_timeout=f"{timeout}s"
            )
        except PrometheusConfigError as exc:
            logger.error(f"Failed to configure prometheus scrape target: {exc}")
            raise exc

    def _on_prometheus_available(self, _: PrometheusConnected) -> None:
        """Trigger configuration of a prometheus scrape target."""
        self.reconfigure_scrape_target()

    #    def _getWorkloadVersion(self):
    #        """Get the microsample workload version from the snapd API via unix-socket."""
    #        snapd_url = f"http+unix://%2Frun%2Fsnapd.socket/v2/snaps/{self._snap_name}"
    #        session = requests_unixsocket.Session()
    #        # Use the requests library to send a GET request over the Unix domain socket
    #        response = session.get(snapd_url)
    #        # Check if the request was successful
    #        if response.status_code == 200:
    #            data = response.json()
    #            workload_version = data["result"]["version"]
    #        else:
    #            workload_version = "unknown"
    #            print(f"Failed to retrieve Snap apps. Status code: {response.status_code}")

    # Return the workload version
    #        return workload_version

    def _get_nics(self):
        nic_dict = {}
        line = self._get_val_from_nova("passthrough_whitelist")
        if line:
            for nic in json.loads(line):
                nic_dict[nic["devname"]] = nic["physical_network"]
            return nic_dict

    def _get_val_from_nova(self, search_str):
        try:
            with open(self._nova_conf, "r") as f:
                for line in f.readlines():
                    if line.startswith(search_str):
                        val = line.split("=")[1]
                        logger.info(f"found {search_str} in {self._nova_conf}: {val}")
                        return val.strip()
                logger.info(f"no {search_str} found in {self._nova_conf}.")
                return ""
        except OSError as e:
            logger.error(f"Could not open/read file {self._nova_conf}")
            raise e

    def _run_cmd(self, args):
        logger.info(f'Running command: {" ".join(args)}')
        cmd = subprocess.run(args, capture_output=True)
        if cmd.returncode != 0:
            logger.error(f'Command {" ".join(args)} returned code {cmd.returncode}, {cmd}')
        return cmd.returncode == 0


if __name__ == "__main__":  # pragma: nocover
    ops.main(PrometheusNumaExporterCharm)  # type: ignore
