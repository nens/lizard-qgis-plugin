# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os.path

from qgis.PyQt.QtCore import QThreadPool
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from lizard_qgis_plugin.communication import UICommunication
from lizard_qgis_plugin.deps.custom_imports import patch_wheel_imports
from lizard_qgis_plugin.utils import count_scenarios_with_name
from lizard_qgis_plugin.widgets.settings import SettingsDialog

try:
    from threedi_scenario_downloader import downloader

    from lizard_qgis_plugin.widgets.lizard_archive_browser import LizardBrowser
except ImportError:
    patch_wheel_imports()
    from threedi_scenario_downloader import downloader

    from lizard_qgis_plugin.widgets.lizard_archive_browser import LizardBrowser


def classFactory(iface):
    return ThreediLizardPlugin(iface)


class ThreediLizardPlugin:
    PLUGIN_NAME = "Lizard"
    PLUGIN_ENTRY_NAME = "ThreediLizard"
    MAX_DOWNLOAD_THREAD_COUNT = 1

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.downloader = downloader
        self.lizard_downloader_pool = QThreadPool()
        self.lizard_downloader_pool.setMaxThreadCount(self.MAX_DOWNLOAD_THREAD_COUNT)
        self.lizard_browser = None
        self.actions = []
        self.menu = self.PLUGIN_NAME
        self.toolbar = self.iface.addToolBar(self.PLUGIN_ENTRY_NAME)
        self.toolbar.setObjectName(self.PLUGIN_ENTRY_NAME)
        self.communication = UICommunication(self.iface, self.PLUGIN_NAME)
        self.settings = SettingsDialog(self)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        """Add a toolbar icon to the toolbar."""

        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.toolbar.addAction(action)

        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)

        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        icon_path = os.path.join(self.plugin_dir, "icon.svg")
        self.add_action(icon_path, text=self.PLUGIN_NAME, callback=self.run, parent=self.iface.mainWindow())
        self.add_action(
            icon_path,
            text="Settings",
            callback=self.show_settings,
            parent=self.iface.mainWindow(),
            add_to_toolbar=False,
        )

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginMenu(self.PLUGIN_NAME, action)
            self.iface.removeToolBarIcon(action)
        # remove the toolbar
        del self.toolbar

    def show_settings(self):
        """Show plugin settings dialog."""
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def run(self):
        """Run method that loads and starts the plugin"""
        self.settings.ensure_api_key_present()
        if not self.settings.api_key:
            return
        if self.lizard_browser is None:
            self.lizard_browser = LizardBrowser(self)
        self.lizard_browser.show()
        self.lizard_browser.raise_()
        self.lizard_browser.activateWindow()
        if self.lizard_browser.lizard_tab.currentIndex() == 0:
            self.lizard_browser.scenario_search_le.setFocus()
        else:
            self.lizard_browser.raster_search_le.setFocus()
