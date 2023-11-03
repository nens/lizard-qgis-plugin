# 3Di Scenario Archive plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
import webbrowser

from qgis.PyQt import uic
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QDialog, QInputDialog

from threedi_scenario_archive.utils import get_api_key_auth_manager, set_api_key_auth_manager


class SettingsDialog(QDialog):
    """Dialog with plugin settings."""

    HTTPS_PREFIX = "https://"
    API_URL_SUFFIX = "/api/v4/"
    WMS_URL_SUFFIX = "/wms/"
    MANAGEMENT_URL_SUFFIX = "/management/"
    DEFAULT_BASE_URL = "nens.lizard.net"

    def __init__(self, plugin, parent=None):
        QDialog.__init__(self, parent)
        plugin_dir = os.path.dirname(os.path.realpath(__file__))
        ui_filepath = os.path.join(plugin_dir, "ui", "settings.ui")
        self.ui = uic.loadUi(ui_filepath, self)
        self.iface = plugin.iface
        self.downloader = plugin.downloader
        self.communication = plugin.communication
        base_url = QSettings().value("threedi_scenario_archive/base_url", self.DEFAULT_BASE_URL)
        self.base_url_le.setText(base_url)
        self.change_base_url_pb.clicked.connect(self.change_base_url)
        self.set_pak_pb.clicked.connect(self.set_personal_api_key)
        self.obtain_pak_pb.clicked.connect(self.obtain_personal_api_key)
        self.ui.close_pb.clicked.connect(self.close)
        self.patch_downloader()
        self.setup_api_key_label()

    def patch_downloader(self):
        """Patch downloader variables and functions."""
        self.downloader.LIZARD_URL = self.api_url
        self.downloader.get_api_key = get_api_key_auth_manager
        self.downloader.set_api_key = set_api_key_auth_manager

    def setup_api_key_label(self):
        """Loading plugin settings from QSettings."""
        if self.api_key:
            self.set_personal_api_key_label(True)
        else:
            self.set_personal_api_key_label(False)

    @property
    def base_url(self):
        url = self.base_url_le.text()
        return url

    @property
    def api_url(self):
        if self.base_url:
            url = f"{self.HTTPS_PREFIX}{self.base_url}{self.API_URL_SUFFIX}"
        else:
            url = f"{self.HTTPS_PREFIX}{self.DEFAULT_BASE_URL}{self.API_URL_SUFFIX}"
        return url

    @property
    def wms_url(self):
        if self.base_url:
            url = f"{self.HTTPS_PREFIX}{self.base_url}{self.WMS_URL_SUFFIX}"
        else:
            url = f"{self.HTTPS_PREFIX}{self.DEFAULT_BASE_URL}{self.WMS_URL_SUFFIX}"
        return url

    @property
    def management_url(self):
        if self.base_url:
            url = f"{self.HTTPS_PREFIX}{self.base_url}{self.MANAGEMENT_URL_SUFFIX}"
        else:
            url = f"{self.HTTPS_PREFIX}{self.DEFAULT_BASE_URL}{self.MANAGEMENT_URL_SUFFIX}"
        return url

    @property
    def api_key(self):
        pak = self.downloader.get_api_key()
        return pak

    def update_lizard_url(self):
        self.downloader.LIZARD_URL = self.api_url

    def update_api_key(self, api_key):
        self.downloader.set_api_key(api_key)

    def change_base_url(self):
        """Change Lizard Base URL."""
        base_url, accept = QInputDialog.getText(self, "Lizard Base URL", "Type Lizard Base URL:")
        if accept is False:
            return
        base_url = base_url.strip("/")
        if base_url.startswith(self.HTTPS_PREFIX):
            base_url = base_url[len(self.HTTPS_PREFIX) :]
        QSettings().setValue("threedi_scenario_archive/base_url", base_url)
        self.base_url_le.setText(base_url)
        self.update_lizard_url()

    def set_personal_api_key(self):
        """Setting active Personal API Key."""
        pak, accept = QInputDialog.getText(self, "Personal API Key", "Paste your Personal API Key:")
        if accept is False:
            return
        self.update_api_key(pak)
        self.set_personal_api_key_label(True)

    def obtain_personal_api_key(self):
        """Open website where user can get his Personal API Key."""
        webbrowser.open(f"{self.MANAGEMENT_URL}/personal_api_keys/")

    def set_personal_api_key_label(self, personal_api_key_available):
        """Setting Personal API Key label text."""
        if personal_api_key_available:
            label_txt = """<html><head/><body><p><span style=" color:#00aa00;">
            ✓ Available</span></p></body></html>"""
        else:
            label_txt = """<html><head/><body><p><span style=" color:#ff0000;">
            ✕ Not found</span></p></body></html>"""
        self.pak_label.setText(label_txt)

    def ensure_api_key_present(self):
        """Check if API key is present."""
        if not self.api_key:
            warn_message = "There is no Lizard API key defined. Please set it up and try again."
            self.communication.show_warn(warn_message)
            self.show()
            self.raise_()
            self.activateWindow()
