# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
from math import ceil
from operator import itemgetter

from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsRasterLayer, QgsRectangle
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QSettings, QSize, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QCheckBox, QFileDialog
from qgis.utils import plugins
from threedi_mi_utils import LocalRevision, LocalSchematisation, list_local_schematisations

from lizard_qgis_plugin.utils import (
    WMSServiceException,
    add_layer_to_group,
    count_scenarios_with_name,
    create_tree_group,
    get_capabilities_layer_uris,
    get_url_raster_instance,
    try_to_write,
)
from lizard_qgis_plugin.workers import ScenarioItemsDownloader

base_dir = os.path.dirname(__file__)
uicls, basecls = uic.loadUiType(os.path.join(base_dir, "ui", "scenario_archive_browser.ui"))


class ScenarioArchiveBrowser(uicls, basecls):
    TABLE_LIMIT = 25
    NAME_COLUMN_IDX = 0
    UUID_COLUMN_IDX = 5
    MAX_THREAD_COUNT = 1

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.plugin = plugin
        self.scenario_model = QStandardItemModel()
        self.scenario_tv.setModel(self.scenario_model)
        self.scenario_results_model = QStandardItemModel()
        self.scenario_results_tv.setModel(self.scenario_results_model)
        self.feedback_model = QStandardItemModel()
        self.feedback_lv.setModel(self.feedback_model)
        self.current_scenario_instances = {}
        self.current_scenario_results = {}
        self.pb_prev_page.clicked.connect(self.previous_scenarios)
        self.pb_next_page.clicked.connect(self.next_scenarios)
        self.page_sbox.valueChanged.connect(self.fetch_scenarios)
        self.pb_add_wms.clicked.connect(self.load_as_wms_layers)
        self.pb_show_files.clicked.connect(self.fetch_results)
        self.pb_download.clicked.connect(self.download_results)
        self.toggle_selection_ckb.stateChanged.connect(self.toggle_results)
        self.pb_clear_feedback.clicked.connect(self.feedback_model.clear)
        self.pb_close.clicked.connect(self.close)
        self.scenario_search_le.returnPressed.connect(self.search_for_scenarios)
        self.scenario_tv.selectionModel().selectionChanged.connect(self.toggle_scenario_selected)
        self.fetch_scenarios()
        self.resize(QSize(1600, 850))

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

    def toggle_scenario_selected(self):
        """Toggle action widgets if any scenario is selected."""
        self.scenario_results_model.clear()
        self.current_scenario_results.clear()
        self.pb_download.setDisabled(True)
        self.grp_raster_settings.setDisabled(True)
        self.toggle_selection_ckb.setChecked(False)
        self.toggle_selection_ckb.setDisabled(True)
        selection_model = self.scenario_tv.selectionModel()
        if selection_model.hasSelection():
            self.pb_add_wms.setEnabled(True)
            self.pb_show_files.setEnabled(True)
        else:
            self.pb_add_wms.setDisabled(True)
            self.pb_show_files.setDisabled(True)

    def previous_scenarios(self):
        """Move to the previous matching scenarios page."""
        self.page_sbox.setValue(self.page_sbox.value() - 1)

    def next_scenarios(self):
        """Move to the next matching scenarios page."""
        self.page_sbox.setValue(self.page_sbox.value() + 1)

    def fetch_scenarios(self):
        """Fetch and list matching scenarios."""
        try:
            searched_text = self.scenario_search_le.text()
            matching_scenarios_count = count_scenarios_with_name(self.plugin.settings.api_url, searched_text)
            pages_nr = ceil(matching_scenarios_count / self.TABLE_LIMIT) or 1
            self.page_sbox.setMaximum(pages_nr)
            self.page_sbox.setSuffix(f" / {pages_nr}")
            self.current_scenario_instances.clear()
            self.scenario_model.clear()
            self.current_scenario_results.clear()
            self.scenario_results_model.clear()
            offset = (self.page_sbox.value() - 1) * self.TABLE_LIMIT
            header = ["Scenario name", "Model name", "Organisation", "User", "Created", "UUID"]
            self.scenario_model.setHorizontalHeaderLabels(header)
            matching_scenarios = self.plugin.downloader.find_scenarios(
                self.TABLE_LIMIT, offset=offset, name__icontains=searched_text
            )
            for scenario_instance in sorted(matching_scenarios, key=itemgetter("created"), reverse=True):
                scenario_uuid = scenario_instance["uuid"]
                uuid_item = QStandardItem(scenario_uuid)
                name_item = QStandardItem(scenario_instance["name"])
                model_name_item = QStandardItem(scenario_instance["model_name"])
                organisation_item = QStandardItem(scenario_instance["organisation"]["name"])
                user_item = QStandardItem(scenario_instance["supplier"])
                created_item = QStandardItem(scenario_instance["created"].split("T")[0])
                scenario_items = [name_item, model_name_item, organisation_item, user_item, created_item, uuid_item]
                self.scenario_model.appendRow(scenario_items)
                self.current_scenario_instances[scenario_uuid] = scenario_instance
            for i in range(len(header)):
                self.scenario_tv.resizeColumnToContents(i)
        except Exception as e:
            self.close()
            error_msg = f"Error: {e}"
            self.plugin.communication.show_error(error_msg)

    def fetch_results(self):
        """Fetch and show selected available scenario result files."""
        index = self.scenario_tv.currentIndex()
        if not index.isValid():
            return
        current_row = index.row()
        scenario_uuid_item = self.scenario_model.item(current_row, self.UUID_COLUMN_IDX)
        scenario_uuid = scenario_uuid_item.text()
        scenario_instance = self.current_scenario_instances[scenario_uuid]
        scenario_results = self.plugin.downloader.get_scenario_instance_results(scenario_uuid)
        self.current_scenario_results.clear()
        self.scenario_results_model.clear()
        header, checkboxes_width = ["Item", "File name"], []
        self.scenario_results_model.setHorizontalHeaderLabels(header)
        for row_number, result in enumerate(scenario_results, start=0):
            result_enabled = True
            result_id = result["id"]
            result_name = result["name"]
            result_attachment_url = result["attachment_url"]
            result_raster = result["raster"]
            if result_raster:
                raster_instance = get_url_raster_instance(self.plugin.downloader.get_api_key(), result_raster)
                if raster_instance["temporal"]:
                    result_enabled = False
                    result_filename = result_name.lower().replace("(timeseries)", "").strip().replace(" ", "_") + ".tif"
                else:
                    result_filename = result_name.lower().replace(" ", "_") + ".tif"
            else:
                result_filename = result_attachment_url.rsplit("/", 1)[-1]
            result_checkbox = QCheckBox(result_name)
            result_checkbox.setEnabled(result_enabled)
            results_checkbox_item = QStandardItem("")
            result_filename_item = QStandardItem(result_filename)
            result_filename_item.setEnabled(result_enabled)
            checkboxes_width.append(result_checkbox.width())
            self.scenario_results_model.appendRow([results_checkbox_item, result_filename_item])
            self.scenario_results_tv.setIndexWidget(self.scenario_results_model.index(row_number, 0), result_checkbox)
            result["checkbox"] = result_checkbox
            result["filename"] = result_filename
            self.current_scenario_results[result_id] = result
        for i in range(len(header)):
            self.scenario_results_tv.resizeColumnToContents(i)
        if checkboxes_width:
            self.scenario_results_tv.setColumnWidth(0, max(checkboxes_width))
        self.pb_download.setEnabled(True)
        self.grp_raster_settings.setEnabled(True)
        self.toggle_selection_ckb.setEnabled(True)
        scenario_crs = QgsCoordinateReferenceSystem.fromOgcWmsCrs(scenario_instance["projection"])
        self.crs_widget.setCrs(scenario_crs)

    def toggle_results(self, checked):
        """Select/deselect all available scenario items."""
        for result in self.current_scenario_results.values():
            checkbox = result["checkbox"]
            if checkbox.isEnabled():
                checkbox.setChecked(checked)

    def select_download_directory(self):
        """Select download directory path widget."""
        download_dir = QFileDialog.getExistingDirectory(self, "Select download directory")
        if download_dir:
            try:
                try_to_write(download_dir)
            except (PermissionError, OSError):
                self.plugin.communication.bar_warn(
                    "Can't write to the selected location. Please select a folder to which you have write permission."
                )
                return
            return download_dir

    def discover_download_directory(self, scenario_instance):
        """Discover download directory."""
        working_dir = QSettings().value("threedi/working_dir", "", type=str)
        if not working_dir:
            working_dir = self.select_download_directory()
        download_dir = working_dir
        model_id = scenario_instance["model_identifier"]
        if not model_id:
            return download_dir
        try:
            import threedi_models_and_simulations.api_calls.threedi_calls as tc
            import threedi_models_and_simulations.deps.custom_imports as ci

            ci.patch_wheel_imports()
            threedi_models_and_simulations = plugins["threedi_models_and_simulations"]
        except (AttributeError, ImportError):
            return download_dir
        ms_settings = threedi_models_and_simulations.plugin_settings
        username, personal_api_token = ms_settings.get_3di_auth()
        threedi_api = tc.get_api_client_with_personal_api_token(personal_api_token, ms_settings.api_url)
        threedi_api_calls = tc.ThreediCalls(threedi_api)
        try:
            local_schematisations = list_local_schematisations(working_dir)
            model_3di = threedi_api_calls.fetch_3di_model(int(model_id))
            model_schematisation_id = model_3di.schematisation_id
            if model_schematisation_id:
                model_schematisation_name = model_3di.schematisation_name
                model_revision_number = model_3di.revision_number
                try:
                    local_schematisation = local_schematisations[model_schematisation_id]
                except KeyError:
                    local_schematisation = LocalSchematisation(
                        working_dir, model_schematisation_id, model_schematisation_name, create=True
                    )
                try:
                    local_revision = local_schematisation.revisions[model_revision_number]
                except KeyError:
                    local_revision = LocalRevision(local_schematisation, model_revision_number)
                    local_revision.make_revision_structure()
                download_dir = local_revision.results_dir
        except Exception as e:
            warn_msg = f"Failed to set proper schematisation revision results folder due to the following error: {e}."
            self.plugin.communication.log_warn(warn_msg)
            download_dir = working_dir
        return download_dir

    def download_results(self):
        """Download selected (checked) result files."""
        index = self.scenario_tv.currentIndex()
        if not index.isValid():
            return
        current_row = index.row()
        scenario_uuid_item = self.scenario_model.item(current_row, self.UUID_COLUMN_IDX)
        scenario_uuid = scenario_uuid_item.text()
        scenario_instance = self.current_scenario_instances[scenario_uuid]
        download_dir = self.discover_download_directory(scenario_instance)
        if not download_dir:
            self.plugin.communication.bar_info("Downloading results files canceled..")
            return
        rasters_to_download, raw_results_to_download = [], []
        for result in self.current_scenario_results.values():
            checkbox = result["checkbox"]
            raster = result["raster"]
            if not checkbox.isChecked():
                continue
            # Remove checkbox before sending to separate thread
            result_copy = {k: v for k, v in result.items() if k != "checkbox"}
            if raster:
                rasters_to_download.append(result_copy)
            else:
                raw_results_to_download.append(result_copy)
        scenario_instance = self.current_scenario_instances[scenario_uuid]
        scenario_name = scenario_instance["name"]
        projection = self.crs_widget.crs().authid()
        no_data = self.no_data_sbox.value()
        scenario_items_downloader = ScenarioItemsDownloader(
            self.plugin.downloader,
            scenario_instance,
            raw_results_to_download,
            rasters_to_download,
            download_dir,
            projection,
            no_data,
        )
        scenario_items_downloader.signals.download_progress.connect(self.on_download_progress)
        scenario_items_downloader.signals.download_finished.connect(self.on_download_finished)
        scenario_items_downloader.signals.download_failed.connect(self.on_download_failed)
        self.plugin.scenario_downloader_pool.start(scenario_items_downloader)
        self.log_feedback(f"Scenario '{scenario_name}' results download task added to the queue.")

    def on_download_progress(self, scenario_instance, progress_message, current_progress, total_progress):
        """Feedback on download progress signal."""
        scenario_name = scenario_instance["name"]
        msg = progress_message if progress_message else f"Downloading files of the scenario: '{scenario_name}'..."
        self.plugin.communication.progress_bar(msg, 0, total_progress, current_progress, clear_msg_bar=True)

    def on_download_finished(self, scenario_instance, downloaded_files, message):
        """Feedback on download finished signal."""
        self.plugin.communication.clear_message_bar()
        self.plugin.communication.bar_info(message)
        self.log_feedback(message)
        file_types_to_add = {"tif", "vrt"}
        files_to_add = [fname for fname in downloaded_files.keys() if fname.rsplit(".", 1)[-1] in file_types_to_add]
        if file_types_to_add:
            scenario_name = scenario_instance["name"]
            scenario_grp = create_tree_group(scenario_name)
            for raster_filename in files_to_add:
                raster_filepath = downloaded_files[raster_filename]
                raster_layer = QgsRasterLayer(raster_filepath, raster_filename, "gdal")
                add_layer_to_group(scenario_grp, raster_layer)
            scenario_grp.setExpanded(False)

    def on_download_failed(self, scenario_instance, error_message):
        """Feedback on download failed signal."""
        self.plugin.communication.clear_message_bar()
        self.plugin.communication.bar_error(error_message)
        self.log_feedback(error_message, Qgis.Critical)

    def search_for_scenarios(self):
        """Method used for searching scenarios with text typed withing search bar."""
        self.page_sbox.valueChanged.disconnect(self.fetch_scenarios)
        self.page_sbox.setValue(1)
        self.page_sbox.valueChanged.connect(self.fetch_scenarios)
        self.fetch_scenarios()

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
