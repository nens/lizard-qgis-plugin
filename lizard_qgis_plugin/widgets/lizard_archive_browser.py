# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
from math import ceil
from operator import itemgetter

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFieldProxyModel,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QSettings, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QFileDialog
from qgis.utils import plugins
from threedi_mi_utils import LocalRevision, LocalSchematisation, list_local_schematisations

from lizard_qgis_plugin.utils import (
    WMSServiceException,
    add_layer_to_group,
    count_rasters_with_name,
    count_scenarios_with_name,
    create_tree_group,
    find_rasters,
    get_capabilities_layer_uris,
    get_url_raster_instance,
    reproject_geometry,
    try_to_write,
)
from lizard_qgis_plugin.workers import RasterDownloader, ScenarioItemsDownloader

base_dir = os.path.dirname(__file__)
lizard_uicls, lizard_basecls = uic.loadUiType(os.path.join(base_dir, "ui", "lizard.ui"))
download_settings_uicls, download_settings_basecls = uic.loadUiType(
    os.path.join(base_dir, "ui", "raster_download_settings.ui")
)


class RasterDownloadSettings(download_settings_uicls, download_settings_basecls):
    def __init__(self, lizard_browser, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.lizard_browser = lizard_browser
        self.clip_polygon_cbo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.clip_name_field_cbo.setFilters(QgsFieldProxyModel.String)
        self.output_dir_raster.fileChanged.connect(self.on_output_dir_changed)
        self.use_polygon_extent_rb.toggled.connect(self.on_extent_changed)
        self.clip_polygon_cbo.layerChanged.connect(self.on_polygon_changed)
        self.clip_name_field_cbo.setLayer(self.clip_polygon_cbo.currentLayer())
        self.accept_pb.clicked.connect(self.accept)
        self.cancel_pb.clicked.connect(self.reject)
        lizard_output_dir = QSettings().value("threedi/last_lizard_output_dir", "", type=str)
        if lizard_output_dir:
            self.output_dir_raster.setFilePath(lizard_output_dir)
        self.populate_selected_raster_settings()

    def on_output_dir_changed(self):
        """Save the last output dir path."""
        output_dir_filepath = self.output_dir_raster.filePath()
        QSettings().setValue("threedi/last_lizard_output_dir", output_dir_filepath)

    def on_extent_changed(self):
        """Enable/disable polygon settings group."""
        if self.use_polygon_extent_rb.isChecked():
            self.grp_polygon_settings.setEnabled(True)
        else:
            self.grp_polygon_settings.setDisabled(True)

    def on_polygon_changed(self, layer):
        """Refresh field list on clip polygon change."""
        self.clip_name_field_cbo.setLayer(layer)

    def populate_selected_raster_settings(self):
        """Populate download settings if any raster is selected."""
        selection_model = self.lizard_browser.raster_tv.selectionModel()
        self.filename_le_raster.clear()
        if selection_model.hasSelection():
            index = self.lizard_browser.raster_tv.currentIndex()
            if index.isValid():
                current_row = index.row()
                raster_uuid_item = self.lizard_browser.raster_model.item(
                    current_row, LizardBrowser.RASTER_UUID_COLUMN_IDX
                )
                raster_uuid = raster_uuid_item.text()
                raster_instance = self.lizard_browser.current_raster_instances[raster_uuid]
                self.filename_le_raster.setText(raster_instance["name"])
                raster_crs = QgsCoordinateReferenceSystem.fromOgcWmsCrs(raster_instance["projection"])
                self.crs_widget_raster.setCrs(raster_crs)
                raster_resolution = raster_instance["pixelsize_x"]
                self.pixel_size_sbox_raster.setValue(raster_resolution if raster_resolution else 0.0)


class LizardBrowser(lizard_uicls, lizard_basecls):
    TABLE_LIMIT = 25
    SCENARIO_NAME_COLUMN_IDX = 0
    SCENARIO_UUID_COLUMN_IDX = 5
    RASTER_NAME_COLUMN_IDX = 1
    RASTER_UUID_COLUMN_IDX = 5
    MAX_THREAD_COUNT = 1

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.plugin = plugin
        self.scenario_model = QStandardItemModel()
        self.scenario_tv.setModel(self.scenario_model)
        self.scenario_results_model = QStandardItemModel()
        self.scenario_results_tv.setModel(self.scenario_results_model)
        self.raster_model = QStandardItemModel()
        self.raster_tv.setModel(self.raster_model)
        self.feedback_model = QStandardItemModel()
        self.feedback_lv.setModel(self.feedback_model)
        self.current_scenario_instances = {}
        self.current_scenario_results = {}
        self.current_raster_instances = {}
        self.pb_prev_page.clicked.connect(self.previous_scenarios)
        self.pb_next_page.clicked.connect(self.next_scenarios)
        self.page_sbox.valueChanged.connect(self.fetch_scenarios)
        self.pb_add_wms.clicked.connect(self.load_scenario_as_wms_layers)
        self.pb_show_files.clicked.connect(self.fetch_results)
        self.pb_download.clicked.connect(self.download_results)
        self.toggle_selection_ckb.stateChanged.connect(self.toggle_results)
        self.scenario_search_le.returnPressed.connect(self.search_for_scenarios)
        self.scenario_tv.selectionModel().selectionChanged.connect(self.toggle_scenario_selected)
        self.pb_prev_page_raster.clicked.connect(self.previous_rasters)
        self.pb_next_page_raster.clicked.connect(self.next_rasters)
        self.page_sbox_raster.valueChanged.connect(self.fetch_rasters)
        self.pb_add_wms_raster.clicked.connect(self.load_raster_as_wms_layers)
        self.pb_download_raster.clicked.connect(self.download_raster_file)
        self.raster_search_le.returnPressed.connect(self.search_for_rasters)
        self.raster_tv.selectionModel().selectionChanged.connect(self.toggle_raster_selected)
        self.pb_clear_feedback.clicked.connect(self.feedback_model.clear)
        self.pb_close.clicked.connect(self.close)
        self.fetch_scenarios()
        self.fetch_rasters()
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

    def search_for_scenarios(self):
        """Method used for searching scenarios with text typed withing search bar."""
        self.page_sbox.valueChanged.disconnect(self.fetch_scenarios)
        self.page_sbox.setValue(1)
        self.page_sbox.valueChanged.connect(self.fetch_scenarios)
        self.fetch_scenarios()

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
                self.plugin.communication.show_warn(
                    "Can't write to the selected location. Please select a folder to which you have write permission."
                )
                self.raise_()
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
        scenario_uuid_item = self.scenario_model.item(current_row, self.SCENARIO_UUID_COLUMN_IDX)
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

    def load_scenario_as_wms_layers(self):
        """Loading selected scenario as a set of the WMS layers."""
        wms_provider = "wms"
        index = self.scenario_tv.currentIndex()
        if index.isValid():
            current_row = index.row()
            scenario_uuid_item = self.scenario_model.item(current_row, self.SCENARIO_UUID_COLUMN_IDX)
            scenario_uuid = scenario_uuid_item.text()
            scenario_name_item = self.scenario_model.item(current_row, self.SCENARIO_NAME_COLUMN_IDX)
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

    def download_results(self):
        """Download selected (checked) result files."""
        index = self.scenario_tv.currentIndex()
        if not index.isValid():
            return
        current_row = index.row()
        scenario_uuid_item = self.scenario_model.item(current_row, self.SCENARIO_UUID_COLUMN_IDX)
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
        self.plugin.lizard_downloader_pool.start(scenario_items_downloader)
        self.log_feedback(f"Scenario '{scenario_name}' results download task added to the queue.")

    def on_download_progress(self, downloaded_item_instance, progress_message, current_progress, total_progress):
        """Feedback on download progress signal."""
        downloaded_item_name = downloaded_item_instance["name"]
        msg = progress_message if progress_message else f"Downloading '{downloaded_item_name}' files..."
        self.plugin.communication.progress_bar(msg, 0, total_progress, current_progress, clear_msg_bar=True)

    def on_download_finished(self, downloaded_item_instance, downloaded_files, message):
        """Feedback on download finished signal."""
        self.plugin.communication.clear_message_bar()
        self.plugin.communication.bar_info(message)
        self.log_feedback(message)
        file_types_to_add = {"tif", "vrt"}
        files_to_add = [fname for fname in downloaded_files.keys() if fname.rsplit(".", 1)[-1] in file_types_to_add]
        if file_types_to_add:
            downloaded_item_name = downloaded_item_instance["name"]
            downloaded_item_grp = create_tree_group(downloaded_item_name)
            for raster_filename in files_to_add:
                raster_filepath = downloaded_files[raster_filename]
                raster_layer = QgsRasterLayer(raster_filepath, raster_filename, "gdal")
                add_layer_to_group(downloaded_item_grp, raster_layer)
            downloaded_item_grp.setExpanded(False)

    def on_download_failed(self, scenario_instance, error_message):
        """Feedback on download failed signal."""
        self.plugin.communication.clear_message_bar()
        self.plugin.communication.bar_error(error_message)
        self.log_feedback(error_message, Qgis.Critical)

    def search_for_rasters(self):
        """Method used for searching rasters with text typed withing search bar."""
        self.page_sbox_raster.valueChanged.disconnect(self.fetch_rasters)
        self.page_sbox_raster.setValue(1)
        self.page_sbox_raster.valueChanged.connect(self.fetch_rasters)
        self.fetch_rasters()

    def previous_rasters(self):
        """Move to the previous matching rasters page."""
        self.page_sbox_raster.setValue(self.page_sbox_raster.value() - 1)

    def next_rasters(self):
        """Move to the next matching rasters page."""
        self.page_sbox_raster.setValue(self.page_sbox_raster.value() + 1)

    def toggle_raster_selected(self):
        """Toggle action widgets if any raster is selected."""
        selection_model = self.raster_tv.selectionModel()
        if selection_model.hasSelection():
            self.pb_add_wms_raster.setEnabled(True)
            index = self.raster_tv.currentIndex()
            if index.isValid():
                current_row = index.row()
                raster_uuid_item = self.raster_model.item(current_row, LizardBrowser.RASTER_UUID_COLUMN_IDX)
                raster_uuid = raster_uuid_item.text()
                raster_instance = self.current_raster_instances[raster_uuid]
                if not raster_instance["temporal"]:
                    self.pb_download_raster.setEnabled(True)
                else:
                    self.pb_download_raster.setDisabled(True)
        else:
            self.pb_add_wms_raster.setDisabled(True)
            self.pb_download_raster.setDisabled(True)

    def fetch_rasters(self):
        """Fetch and list matching rasters."""
        try:
            searched_text = self.raster_search_le.text()
            matching_rasters_count = count_rasters_with_name(self.plugin.settings.api_url, searched_text)
            pages_nr = ceil(matching_rasters_count / self.TABLE_LIMIT) or 1
            self.page_sbox_raster.setMaximum(pages_nr)
            self.page_sbox_raster.setSuffix(f" / {pages_nr}")
            self.current_raster_instances.clear()
            self.raster_model.clear()
            offset = (self.page_sbox_raster.value() - 1) * self.TABLE_LIMIT
            header = ["🕒", "Name", "Description", "Organisation", "Last update", "UUID"]
            self.raster_model.setHorizontalHeaderLabels(header)
            matching_rasters = find_rasters(
                self.plugin.settings.api_url, self.TABLE_LIMIT, offset=offset, name__icontains=searched_text
            )
            for raster_instance in sorted(matching_rasters, key=itemgetter("created"), reverse=True):
                raster_uuid = raster_instance["uuid"]
                uuid_item = QStandardItem(raster_uuid)
                temporal_item = QStandardItem("🕒" if raster_instance["temporal"] else "")
                name_item = QStandardItem(raster_instance["name"])
                description_item = QStandardItem(raster_instance["description"])
                organisation_item = QStandardItem(raster_instance["organisation"]["name"])
                last_updated_item = QStandardItem(raster_instance["last_modified"].split("T")[0])
                raster_items = [
                    temporal_item,
                    name_item,
                    description_item,
                    organisation_item,
                    last_updated_item,
                    uuid_item,
                ]
                self.raster_model.appendRow(raster_items)
                self.current_raster_instances[raster_uuid] = raster_instance
            for i in range(len(header)):
                self.raster_tv.resizeColumnToContents(i)
        except Exception as e:
            self.close()
            error_msg = f"Error: {e}"
            self.plugin.communication.show_error(error_msg)

    def load_raster_as_wms_layers(self):
        """Loading selected scenario as a set of the WMS layers."""
        wms_provider = "wms"
        index = self.raster_tv.currentIndex()
        if index.isValid():
            current_row = index.row()
            raster_uuid_item = self.raster_model.item(current_row, self.RASTER_UUID_COLUMN_IDX)
            raster_uuid = raster_uuid_item.text()
            raster_name_item = self.raster_model.item(current_row, self.RASTER_NAME_COLUMN_IDX)
            raster_name = raster_name_item.text()
            get_capabilities_url = f"{self.plugin.settings.wms_url}raster_{raster_uuid}/?request=GetCapabilities"
            layers_to_add = []
            try:
                for layer_name, layer_uri in get_capabilities_layer_uris(get_capabilities_url):
                    layer = QgsRasterLayer(layer_uri, layer_name, wms_provider)
                    layers_to_add.append(layer)
            except WMSServiceException as e:
                error_message = f"Loading of the requested raster WMS layers failed due to following error:\n{e}"
                self.log_feedback(error_message, Qgis.Critical)
                self.plugin.communication.show_error(error_message)
                return
            raster_group = create_tree_group(raster_name)
            extent = QgsRectangle()
            extent.setMinimal()
            for wms_layer in layers_to_add:
                extent.combineExtentWith(wms_layer.extent())
                wms_layer.setCustomProperty("identify/format", "Text")
                add_layer_to_group(raster_group, wms_layer)
            map_canvas = self.plugin.iface.mapCanvas()
            map_canvas.setExtent(extent)
            map_canvas.refresh()
            self.log_feedback(f"WMS layers for raster '{raster_name}' added to the project.")

    def download_raster_file(self):
        """Download selected raster."""
        index = self.raster_tv.currentIndex()
        if not index.isValid():
            return
        download_settings_dlg = RasterDownloadSettings(self)
        res = download_settings_dlg.exec_()
        if res != QDialog.Accepted:
            self.raise_()
            return
        current_row = index.row()
        raster_uuid_item = self.raster_model.item(current_row, self.RASTER_UUID_COLUMN_IDX)
        raster_uuid = raster_uuid_item.text()
        raster_instance = self.current_raster_instances[raster_uuid]
        download_dir = download_settings_dlg.output_dir_raster.filePath()
        raster_name = download_settings_dlg.filename_le_raster.text()
        no_data = download_settings_dlg.no_data_sbox_raster.value()
        resolution = download_settings_dlg.pixel_size_sbox_raster.value()
        resolution = resolution if resolution else None
        projection = download_settings_dlg.crs_widget_raster.crs().authid()
        target_crs = QgsCoordinateReferenceSystem.fromOgcWmsCrs(projection)
        if not download_dir:
            self.plugin.communication.show_warn("Output directory not specified - please specify it and try again.")
            self.raise_()
            QTimer.singleShot(1, self.download_raster_file)
            return
        if not raster_name:
            self.plugin.communication.show_warn("Output filename not specified - please specify it and try again.")
            self.raise_()
            QTimer.singleShot(1, self.download_raster_file)
            return
        try:
            try_to_write(download_dir)
        except (PermissionError, OSError):
            self.plugin.communication.show_warn(
                "Can't write to the selected location. Please select a folder to which you have write permission."
            )
            self.raise_()
            QTimer.singleShot(1, self.download_raster_file)
            return
        # If map canvas extent
        if download_settings_dlg.use_canvas_extent_rb.isChecked():
            project_crs = QgsProject.instance().crs()
            polygon_id, polygon_name = 0, ""
            polygon_wkt = self.plugin.iface.mapCanvas().extent().asWktPolygon()
            polygon_geom = QgsGeometry.fromWkt(polygon_wkt)
            polygon_geom = reproject_geometry(polygon_geom, project_crs, target_crs)
            named_extent_polygons = {(polygon_id, polygon_name): polygon_geom.asWkt()}
            crop_to_polygon = False
        # If polygon extent
        else:
            polygon_layer = download_settings_dlg.clip_polygon_cbo.currentLayer()
            if not polygon_layer:
                self.plugin.communication.show_warn(
                    "Clip polygon layer is not specified - please specify it and try again."
                )
                self.raise_()
                QTimer.singleShot(1, self.download_raster_file)
                return
            polygon_name_field = download_settings_dlg.clip_name_field_cbo.currentField()
            if download_settings_dlg.selected_features_ckb.isChecked():
                features_iterator = polygon_layer.selectedFeatures()
                number_of_features = polygon_layer.selectedFeatureCount()
            else:
                features_iterator = polygon_layer.getFeatures()
                number_of_features = polygon_layer.featureCount()
            if number_of_features == 0:
                self.plugin.communication.show_warn("There are no clip features defined - raster downloading canceled.")
                self.raise_()
                return
            if number_of_features > 1 and not polygon_name_field:
                self.plugin.communication.show_warn(
                    "Cannot download rasters for multiple polygons if name field is not specified. "
                    "Either select a single polygon and use the option 'Selected features only', "
                    "or specify a name field and try again."
                )
                self.raise_()
                QTimer.singleShot(1, self.download_raster_file)
                return
            polygon_layer_crs = polygon_layer.crs()
            named_extent_polygons = {}
            for feat in features_iterator:
                fid = feat.id()
                try:
                    polygon_name = feat[polygon_name_field]
                except KeyError:
                    polygon_name = raster_name
                polygon_wkt = reproject_geometry(feat.geometry(), polygon_layer_crs, target_crs).asWkt()
                named_extent_polygons[fid, polygon_name] = polygon_wkt
            crop_to_polygon = download_settings_dlg.clip_to_polygon_ckb.isChecked()
        # Spawn raster downloading task
        raster_downloader = RasterDownloader(
            self.plugin.downloader,
            raster_instance,
            raster_name,
            download_dir,
            named_extent_polygons,
            crop_to_polygon,
            no_data,
            resolution,
            projection,
        )
        raster_downloader.signals.download_progress.connect(self.on_download_progress)
        raster_downloader.signals.download_finished.connect(self.on_download_finished)
        raster_downloader.signals.download_failed.connect(self.on_download_failed)
        self.plugin.lizard_downloader_pool.start(raster_downloader)
        self.log_feedback(f"Raster '{raster_name}' download task added to the queue.")
