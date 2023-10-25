# 3Di Scenario Archive plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
from qgis.core import QgsApplication, QgsAuthMethodConfig
from qgis.PyQt.QtCore import QSettings


def get_api_key_authcfg():
    """Getting 3Di Scenario Archive credentials from the QGIS Authorization Manager."""
    settings = QSettings()
    authcfg = settings.value("threedi_scenario_archive/authcfg", None)
    auth_manager = QgsApplication.authManager()
    cfg = QgsAuthMethodConfig()
    auth_manager.loadAuthenticationConfig(authcfg, cfg, True)
    api_key = cfg.config("password")
    return api_key


def set_api_key_authcfg(api_key):
    """Setting 3Di Scenario Archive credentials in the QGIS Authorization Manager."""
    username = "__key__"
    settings = QSettings()
    authcfg = settings.value("threedi_scenario_archive/authcfg", None)
    cfg = QgsAuthMethodConfig()
    auth_manager = QgsApplication.authManager()
    auth_manager.setMasterPassword()
    auth_manager.loadAuthenticationConfig(authcfg, cfg, True)

    if cfg.id():
        cfg.setConfig("username", username)
        cfg.setConfig("password", api_key)
        auth_manager.updateAuthenticationConfig(cfg)
    else:
        cfg.setMethod("Basic")
        cfg.setName("Lizard Api Key")
        cfg.setConfig("username", username)
        cfg.setConfig("password", api_key)
        auth_manager.storeAuthenticationConfig(cfg)
        settings.setValue("threedi_scenario_archive/authcfg", cfg.id())
