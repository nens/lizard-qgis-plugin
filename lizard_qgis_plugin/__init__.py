# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import pyplugin_installer
from qgis.core import QgsSettings
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.utils import isPluginLoaded, startPlugin

def check_dependency_loader():
    required_plugin = "nens_dependency_loader"
    if not isPluginLoaded(required_plugin):
        if (
            QMessageBox.question(
                None,
                "N&S Dependency Loader",
                "N&S Dependency Loader is required, but not loaded. Would you like to load it?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            try:  # This is basically what qgis.utils.loadPlugin() does, but that also shows errors, so we need to do it explicitly
                __import__(required_plugin)
                plugin_loadable = True
            except:
                plugin_loadable = False

            if plugin_loadable:
                if not startPlugin(required_plugin):
                    QMessageBox.warning(
                        None,
                        "N&S Dependency Loader",
                        "Unable to start N&S Dependency Loader, please enable the plugin manually",
                    )
                    return
            else:
                pyplugin_installer.instance().fetchAvailablePlugins(True)
                pyplugin_installer.instance().installPlugin(required_plugin)

            QgsSettings().setValue("/PythonPlugins/" + required_plugin, True)
            QgsSettings().remove("/PythonPlugins/watchDogTimestamp/" + required_plugin)


def classFactory(iface):
    check_dependency_loader()
    from .lizard_plugin import ThreediLizardPlugin
    return ThreediLizardPlugin(iface)
