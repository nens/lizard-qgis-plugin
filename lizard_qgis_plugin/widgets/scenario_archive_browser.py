# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
from math import ceil
from operator import itemgetter

from qgis.core import Qgis, QgsRasterLayer, QgsRectangle
from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel

from lizard_qgis_plugin.utils import (
    WMSServiceException,
    add_layer_to_group,
    count_scenarios_with_name,
    create_tree_group,
    get_capabilities_layer_uris,
)

base_dir = os.path.dirname(__file__)
uicls, basecls = uic.loadUiType(os.path.join(base_dir, "ui", "scenario_archive_browser.ui"))


class ScenarioArchiveBrowser(uicls, basecls):
    TABLE_LIMIT = 25
    NAME_COLUMN_IDX = 0
    UUID_COLUMN_IDX = 5

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.plugin = plugin
        self.scenario_model = QStandardItemModel()
        self.scenario_tv.setModel(self.scenario_model)
        self.feedback_model = QStandardItemModel()
        self.feedback_lv.setModel(self.feedback_model)
        self.pb_prev_page.clicked.connect(self.previous_scenarios)
        self.pb_next_page.clicked.connect(self.next_scenarios)
        self.page_sbox.valueChanged.connect(self.get_scenarios)
        self.pb_add_wms.clicked.connect(self.load_as_wms_layers)
        self.pb_close.clicked.connect(self.close)
        self.pb_clear_feedback.clicked.connect(self.feedback_model.clear)
        self.scenario_search_le.returnPressed.connect(self.search_for_scenarios)
        self.scenario_tv.selectionModel().selectionChanged.connect(self.toggle_add_wms)
        self.get_scenarios()

    def log_feedback(self, feedback_message, level=Qgis.Info):
        """Log messages in the feedback list view."""
        if level == Qgis.Info:
            color = QColor(Qt.darkGreen)
        elif level == Qgis.Warning:
            color = QColor(Qt.darkYellow)
        else:
            color = QColor(Qt.red)
        brush = QBrush(color)
        feedback_item = QStandardItem(feedback_message)
        feedback_item.setForeground(brush)
        self.feedback_model.appendRow([feedback_item])

    def toggle_add_wms(self):
        """Toggle add as WMS button if any scenario is selected."""
        selection_model = self.scenario_tv.selectionModel()
        if selection_model.hasSelection():
            self.pb_add_wms.setEnabled(True)
        else:
            self.pb_add_wms.setDisabled(True)

    def previous_scenarios(self):
        """Moving to the previous matching scenarios page."""
        self.page_sbox.setValue(self.page_sbox.value() - 1)

    def next_scenarios(self):
        """Moving to the next matching scenarios page."""
        self.page_sbox.setValue(self.page_sbox.value() + 1)

    def get_scenarios(self):
        """Fetching and listing matching scenarios."""
        try:
            searched_text = self.scenario_search_le.text()
            matching_scenarios_count = count_scenarios_with_name(self.plugin.settings.api_url, searched_text)
            pages_nr = ceil(matching_scenarios_count / self.TABLE_LIMIT) or 1
            self.page_sbox.setMaximum(pages_nr)
            self.page_sbox.setSuffix(f" / {pages_nr}")
            self.scenario_model.clear()
            offset = (self.page_sbox.value() - 1) * self.TABLE_LIMIT
            header = ["Scenario name", "Model name", "Organisation", "User", "Created", "UUID"]
            self.scenario_model.setHorizontalHeaderLabels(header)
            matching_scenarios = self.plugin.downloader.find_scenarios(
                self.TABLE_LIMIT, offset=offset, name__icontains=searched_text
            )
            for scenario in sorted(matching_scenarios, key=itemgetter("created"), reverse=True):
                name_item = QStandardItem(scenario["name"])
                model_name_item = QStandardItem(scenario["model_name"])
                organisation_item = QStandardItem(scenario["organisation"]["name"])
                user_item = QStandardItem(scenario["supplier"])
                created_item = QStandardItem(scenario["created"].split("T")[0])
                uuid_item = QStandardItem(scenario["uuid"])
                self.scenario_model.appendRow(
                    [name_item, model_name_item, organisation_item, user_item, created_item, uuid_item]
                )
            for i in range(len(header)):
                self.scenario_tv.resizeColumnToContents(i)
        except Exception as e:
            self.close()
            error_msg = f"Error: {e}"
            self.plugin.communication.show_error(error_msg)

    def search_for_scenarios(self):
        """Method used for searching scenarios with text typed withing search bar."""
        self.page_sbox.valueChanged.disconnect(self.get_scenarios)
        self.page_sbox.setValue(1)
        self.page_sbox.valueChanged.connect(self.get_scenarios)
        self.get_scenarios()

    def load_as_wms_layers(self):
        """Loading selected scenario as a set of the WMS layers."""
        wms_provider = "wms"
        index = self.scenario_tv.currentIndex()
        if index.isValid():
            current_row = index.row()
            scenario_uuid_item = self.scenario_model.item(current_row, self.UUID_COLUMN_IDX)
            scenario_uuid = scenario_uuid_item.text()
            scenario_name_item = self.scenario_model.item(current_row, self.NAME_COLUMN_IDX)
            scenario_name = scenario_name_item.text()
            get_capabilities_url = f"{self.plugin.settings.wms_url}scenario_{scenario_uuid}/?request=GetCapabilities"
            layers_to_add = []
            try:
                for layer_name, layer_uri in get_capabilities_layer_uris(get_capabilities_url):
                    layer = QgsRasterLayer(layer_uri, layer_name, wms_provider)
                    layers_to_add.append(layer)
            except WMSServiceException as e:
                error_message = f"Loading of the requested scenario WMS layers failed due to following error:\n{e}"
                self.log_feedback(error_message, Qgis.Critical)
                self.plugin.communication.show_error(error_message)
                return
            scenario_group = create_tree_group(scenario_name)
            extent = QgsRectangle()
            extent.setMinimal()
            for wms_layer in layers_to_add:
                extent.combineExtentWith(wms_layer.extent())
                wms_layer.setCustomProperty("identify/format", "Text")
                add_layer_to_group(scenario_group, wms_layer)
            map_canvas = self.plugin.iface.mapCanvas()
            map_canvas.setExtent(extent)
            map_canvas.refresh()
            self.log_feedback(f"WMS layers for scenario '{scenario_name}' added to the project.")
