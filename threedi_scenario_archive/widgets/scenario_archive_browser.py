# 3Di Scenario Archive plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
from qgis.PyQt import uic

base_dir = os.path.dirname(__file__)
uicls, basecls = uic.loadUiType(os.path.join(base_dir, "ui", "scenario_archive_browser.ui"))


class ScenarioArchiveBrowser(uicls, basecls):

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.plugin = plugin
        self.pb_close.clicked.connect(self.reject)
