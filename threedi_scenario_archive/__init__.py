# 3Di Scenario Archive plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os.path

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from threedi_scenario_downloader import downloader

from threedi_scenario_archive.communication import UICommunication
from threedi_scenario_archive.deps.custom_imports import patch_wheel_imports
from threedi_scenario_archive.utils import count_scenarios_with_name
from threedi_scenario_archive.widgets.scenario_archive_browser import ScenarioArchiveBrowser
from threedi_scenario_archive.widgets.settings import SettingsDialog


def classFactory(iface):
    return ThreediScenarioArchivePlugin(iface)


class ThreediScenarioArchivePlugin:
    PLUGIN_NAME = "3Di Scenario Archive"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.downloader = downloader
        self.actions = []
        self.menu = self.PLUGIN_NAME
        self.toolbar = self.iface.addToolBar("ThreediScenarioArchive")
        self.toolbar.setObjectName("ThreediScenarioArchive")
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

    def run(self):
        """Run method that loads and starts the plugin"""
        patch_wheel_imports()
        self.settings.ensure_api_key_present()
        if not self.settings.api_key:
            return
        scenario_browser = ScenarioArchiveBrowser(self)
        scenario_browser.exec_()
