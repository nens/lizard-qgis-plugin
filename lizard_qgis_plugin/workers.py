# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
import tempfile
import time
import uuid
from collections import defaultdict

import requests
from qgis.core import QgsGeometry
from qgis.PyQt.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot
from threedi_mi_utils import bypass_max_path_limit

from lizard_qgis_plugin.utils import (
    build_vrt,
    clean_up_buildings_result,
    clip_raster,
    create_buildings_flood_risk_task,
    create_buildings_result,
    create_raster_tasks,
    create_vulnerable_buildings_result,
    layer_to_gpkg,
    split_raster_extent,
    split_scenario_extent,
    translate_illegal_chars,
    upload_local_file,
    wkt_polygon_layer,
)


class LizardDownloadError(Exception):
    """Lizard files downloader exception class."""

    pass


class LizardFloodRiskAnalysisError(Exception):
    """Lizard flood risk analyzer exception class."""

    pass


class LizardDownloaderSignals(QObject):
    """Definition of the items download worker signals."""

    download_progress = pyqtSignal(dict, str, int, int)
    download_finished = pyqtSignal(dict, dict, str)
    download_failed = pyqtSignal(dict, str)


class LizardFloodRiskAnalysisSignals(QObject):
    """Definition of the buildings flood risk analyzer worker signals."""

    analysis_progress = pyqtSignal(dict, str, int, int)
    analysis_finished = pyqtSignal(dict, str)
    analysis_failed = pyqtSignal(dict, str)


class ScenarioItemsDownloader(QRunnable):
    """Worker object responsible for downloading scenario files."""

    TASK_CHECK_SLEEP_TIME = 5

    def __init__(
        self,
        downloader,
        scenario_instance,
        raw_results_to_download,
        raster_results,
        download_dir,
        no_data,
        resolution,
        projection,
    ):
        super().__init__()
        self.downloader = downloader
        self.scenario_instance = scenario_instance
        self.scenario_id = scenario_instance["uuid"]
        self.scenario_name = scenario_instance["name"]
        self.scenario_simulation_id = int(scenario_instance["simulation_identifier"])
        self.raw_results_to_download = raw_results_to_download
        self.raster_results = raster_results
        self.scenario_download_dir = os.path.join(
            download_dir, translate_illegal_chars(f"{self.scenario_name} ({self.scenario_simulation_id})")
        )
        self.no_data = no_data
        self.resolution = resolution
        self.projection = projection
        self.total_progress = 100
        self.current_step = 0
        self.number_of_steps = 0
        if raw_results_to_download:
            self.number_of_steps += len(raw_results_to_download)
        if raster_results:
            self.number_of_steps += len(self.raster_results) + 1  # Extra step for spawning raster creation tasks
        self.percentage_per_step = self.total_progress / self.number_of_steps
        self.signals = LizardDownloaderSignals()
        self.downloaded_files = {}

    def download_raw_results(self):
        for result in self.raw_results_to_download:
            attachment_url = result["attachment_url"]
            attachment_filename = result["filename"]
            target_filepath = bypass_max_path_limit(os.path.join(self.scenario_download_dir, attachment_filename))
            progress_msg = f"Downloading '{attachment_filename}' (scenario: '{self.scenario_name}')..."
            self.report_progress(progress_msg)
            try:
                self.downloader.download_file(attachment_url, target_filepath)
            except Exception as e:
                error_msg = f"Download of the {attachment_filename} failed due to the following error: {e}"
                raise LizardDownloadError(error_msg)
            self.downloaded_files[attachment_filename] = target_filepath

    def download_raster_results(self):
        task_raster_results, processed_tasks = {}, {}
        success_statuses = {"SUCCESS"}
        in_progress_statuses = {"PENDING", "UNKNOWN", "STARTED", "RETRY"}
        # Create tasks
        progress_msg = f"Spawning raster tasks and preparing for download (scenario: '{self.scenario_name}')..."
        self.report_progress(progress_msg)
        spatial_bounds = split_scenario_extent(self.scenario_instance, self.resolution)
        for raster_result in self.raster_results:
            raster_url = raster_result["raster"]
            lizard_url = self.downloader.LIZARD_URL
            api_key = self.downloader.get_api_key()
            raster_response = requests.get(url=raster_url, auth=("__key__", api_key))
            raster_response.raise_for_status()
            raster = raster_response.json()
            original_raster_filename = raster_result["filename"]
            raster_name, raster_extension = original_raster_filename.rsplit(".", 1)
            tasks = create_raster_tasks(lizard_url, api_key, raster, spatial_bounds, self.projection, self.no_data)
            is_chunked_raster = len(tasks) > 1
            for raster_task_idx, task in enumerate(tasks, 1):
                task_id = task["task_id"]
                if is_chunked_raster:
                    raster_filename = f"{raster_name}_{raster_task_idx:02d}.{raster_extension}"
                    raster_result_copy = {k: v for k, v in raster_result.items()}
                    raster_result_copy["filename"] = raster_filename
                    task_raster_results[task_id] = raster_result_copy
                else:
                    task_raster_results[task_id] = raster_result
                processed_tasks[task_id] = False
        # Check status of task and download
        while not all(processed_tasks.values()):
            for task_id, processed in processed_tasks.items():
                if processed:
                    continue
                task_status = self.downloader.get_task_status(task_id)
                if task_status in success_statuses:
                    processed_tasks[task_id] = True
                elif task_status in in_progress_statuses:
                    continue
                else:
                    error_msg = f"Task {task_id} failed, status was: {task_status}"
                    raise LizardDownloadError(error_msg)
            time.sleep(self.TASK_CHECK_SLEEP_TIME)
        # Download tasks files
        rasters_per_code = defaultdict(list)
        for task_id, raster_result in sorted(task_raster_results.items(), key=lambda x: x[1]["filename"]):
            raster_filename = raster_result["filename"]
            raster_code = raster_result["code"]
            progress_msg = f"Downloading '{raster_filename}' (scenario: '{self.scenario_name}')..."
            self.report_progress(progress_msg, increase_current_step=raster_code not in rasters_per_code)
            raster_filepath = bypass_max_path_limit(os.path.join(self.scenario_download_dir, raster_filename))
            self.downloader.download_task(task_id, raster_filepath)
            rasters_per_code[raster_code].append(raster_filepath)
            self.downloaded_files[raster_filename] = raster_filepath
        self.report_progress(progress_msg, increase_current_step=False)
        vrt_options = {"resolution": "average", "resampleAlg": "nearest", "srcNodata": self.no_data}
        for raster_code, raster_filepaths in rasters_per_code.items():
            if len(raster_filepaths) > 1:
                raster_filepaths.sort()
                first_raster_filepath = raster_filepaths[0]
                vrt_filepath = first_raster_filepath.replace("_01", "").replace(".tif", ".vrt")
                vrt_filename = os.path.basename(vrt_filepath)
                progress_msg = f"Building VRT: '{vrt_filepath}'..."
                self.report_progress(progress_msg, increase_current_step=False)
                build_vrt(vrt_filepath, raster_filepaths, **vrt_options)
                self.downloaded_files[vrt_filename] = vrt_filepath
                for raster_filepath in raster_filepaths:
                    raster_filename = os.path.basename(raster_filepath)
                    del self.downloaded_files[raster_filename]

    @pyqtSlot()
    def run(self):
        """Downloading simulation results files."""
        try:
            self.report_progress(increase_current_step=False)
            if not os.path.exists(self.scenario_download_dir):
                os.makedirs(self.scenario_download_dir)
            self.download_raw_results()
            self.download_raster_results()
            self.report_finished(
                "Scenario items download finished. " f"Downloaded items are in: {self.scenario_download_dir}"
            )
        except LizardDownloadError as e:
            self.report_failure(str(e))
        except Exception as e:
            error_msg = f"Download failed due to the following error: {e}"
            self.report_failure(error_msg)

    def report_progress(self, progress_message=None, increase_current_step=True):
        """Report worker progress."""
        current_progress = int(self.current_step * self.percentage_per_step)
        if increase_current_step:
            self.current_step += 1
        self.signals.download_progress.emit(
            self.scenario_instance, progress_message, current_progress, self.total_progress
        )

    def report_failure(self, error_message):
        """Report worker failure message."""
        self.signals.download_failed.emit(self.scenario_instance, error_message)

    def report_finished(self, message):
        """Report worker finished message."""
        self.signals.download_finished.emit(self.scenario_instance, self.downloaded_files, message)


class RasterDownloader(QRunnable):
    """Worker object responsible for downloading rasters."""

    TASK_CHECK_SLEEP_TIME = 5

    def __init__(
        self,
        downloader,
        raster_instance,
        raster_name,
        download_dir,
        named_extent_polygons,
        crop_to_polygons,
        no_data,
        resolution,
        projection,
    ):
        super().__init__()
        self.downloader = downloader
        self.raster_instance = raster_instance
        self.raster_id = raster_instance["uuid"]
        self.raster_name = raster_name
        self.raster_download_dir = os.path.join(download_dir, str(self.raster_name))
        self.named_extent_polygons = named_extent_polygons
        self.crop_to_polygons = crop_to_polygons
        self.no_data = no_data
        self.resolution = resolution
        self.projection = projection
        self.total_progress = 100
        self.current_step = 0
        self.number_of_steps = len(self.named_extent_polygons) + 1  # Extra step for spawning raster creation tasks
        self.percentage_per_step = self.total_progress / self.number_of_steps
        self.signals = LizardDownloaderSignals()
        self.downloaded_files = {}

    def download_raster_files(self):
        raster_tasks, processed_tasks = {}, {}
        success_statuses = {"SUCCESS"}
        in_progress_statuses = {"PENDING", "UNKNOWN", "STARTED", "RETRY"}
        # Create tasks
        progress_msg = f"Spawning raster tasks and preparing for download (raster: '{self.raster_name}')..."
        self.report_progress(progress_msg)
        lizard_url = self.downloader.LIZARD_URL
        api_key = self.downloader.get_api_key()
        # Raster tasks for each extent
        for polygon_key, polygon_wkt in self.named_extent_polygons.items():
            polygon_fid, polygon_name = polygon_key
            raster_name = f"{self.raster_name} {polygon_fid} {polygon_name}" if polygon_name else self.raster_name
            polygon_geometry = QgsGeometry.fromWkt(polygon_wkt)
            bbox = polygon_geometry.boundingBox()
            bbox_as_list = [bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()]
            spatial_bounds = split_raster_extent(self.raster_instance, bbox_as_list, self.resolution)
            tasks = create_raster_tasks(
                lizard_url, api_key, self.raster_instance, spatial_bounds, self.projection, self.no_data
            )
            is_chunked_raster = len(tasks) > 1
            raster_tasks[polygon_key] = {}
            for raster_task_idx, task in enumerate(tasks, 1):
                task_id = task["task_id"]
                if is_chunked_raster:
                    raster_filename = f"{raster_name}_{raster_task_idx:02d}.tif"
                else:
                    raster_filename = f"{raster_name}.tif"
                raster_tasks[polygon_key][task_id] = raster_filename
                processed_tasks[task_id] = False
        # Check status of task and download
        while not all(processed_tasks.values()):
            for task_id, processed in processed_tasks.items():
                if processed:
                    continue
                task_status = self.downloader.get_task_status(task_id)
                if task_status in success_statuses:
                    processed_tasks[task_id] = True
                elif task_status in in_progress_statuses:
                    continue
                else:
                    error_msg = f"Task {task_id} failed, status was: {task_status}"
                    raise LizardDownloadError(error_msg)
            time.sleep(self.TASK_CHECK_SLEEP_TIME)
        # Download tasks files
        for polygon_key, polygon_tasks in raster_tasks.items():
            polygon_raster_filepaths = []
            for task_id, raster_filename in polygon_tasks.items():
                progress_msg = f"Downloading '{raster_filename}' (raster: '{self.raster_name}')..."
                self.report_progress(progress_msg, increase_current_step=False)
                raster_filepath = bypass_max_path_limit(os.path.join(self.raster_download_dir, raster_filename))
                raster_url = self.downloader.get_task_download_url(task_id)
                self.downloader.download_file(raster_url, raster_filepath)
                self.downloaded_files[raster_filename] = raster_filepath
                polygon_raster_filepaths.append(raster_filepath)
            self.report_progress(progress_msg, increase_current_step=True)
            # Clip raster with clip polygon if option checked.
            if self.crop_to_polygons:
                temp_dir = tempfile.gettempdir()
                crop_polygon_wkt = self.named_extent_polygons[polygon_key]
                clip_polygon_layer = wkt_polygon_layer(crop_polygon_wkt, epsg=self.projection)
                temp_clip_gpkg = os.path.join(temp_dir, f"clip_polygon_{uuid.uuid4()}.gpkg")
                layer_to_gpkg(clip_polygon_layer, temp_clip_gpkg, overwrite=True)
                for raster_filepath in polygon_raster_filepaths:
                    clip_raster(raster_filepath, temp_clip_gpkg, no_data=self.no_data)
            # Build VRT if needed
            vrt_options = {"resolution": "average", "resampleAlg": "nearest", "srcNodata": self.no_data}
            if len(polygon_tasks) > 1:
                first_raster_filepath = polygon_raster_filepaths[0]
                vrt_filepath = first_raster_filepath.replace("_01", "").replace(".tif", ".vrt")
                vrt_filename = os.path.basename(vrt_filepath)
                progress_msg = f"Building VRT: '{vrt_filepath}'..."
                self.report_progress(progress_msg, increase_current_step=False)
                build_vrt(vrt_filepath, polygon_raster_filepaths, **vrt_options)
                self.downloaded_files[vrt_filename] = vrt_filepath
                for raster_filepath in polygon_raster_filepaths:
                    raster_filename = os.path.basename(raster_filepath)
                    del self.downloaded_files[raster_filename]

    @pyqtSlot()
    def run(self):
        """Downloading simulation results files."""
        try:
            self.report_progress(increase_current_step=False)
            if not os.path.exists(self.raster_download_dir):
                os.makedirs(self.raster_download_dir)
            self.download_raster_files()
            self.report_finished("Raster download finished. " f"Downloaded files are in: {self.raster_download_dir}")
        except LizardDownloadError as e:
            self.report_failure(str(e))
        except Exception as e:
            error_msg = f"Download failed due to the following error: {e}"
            self.report_failure(error_msg)

    def report_progress(self, progress_message=None, increase_current_step=True):
        """Report worker progress."""
        current_progress = int(self.current_step * self.percentage_per_step)
        if increase_current_step:
            self.current_step += 1
        self.signals.download_progress.emit(
            self.raster_instance, progress_message, current_progress, self.total_progress
        )

    def report_failure(self, error_message):
        """Report worker failure message."""
        self.signals.download_failed.emit(self.raster_instance, error_message)

    def report_finished(self, message):
        """Report worker finished message."""
        self.signals.download_finished.emit(self.raster_instance, self.downloaded_files, message)


class BuildingsFloodRiskAnalyzer(QRunnable):
    """Worker object responsible for running building flood risk analysis."""

    TASK_CHECK_SLEEP_TIME = 5

    def __init__(
        self,
        downloader,
        scenario_instance,
        buildings_gpkg,
        calculation_method="dgbc",
        output_format="gpkg",
    ):
        super().__init__()
        self.downloader = downloader
        self.scenario_instance = scenario_instance
        self.scenario_name = self.scenario_instance["name"]
        self.buildings_gpkg = buildings_gpkg
        self.calculation_method = calculation_method
        self.output_format = output_format
        self.total_progress = 100
        self.current_step = 0
        self.number_of_steps = 5
        self.percentage_per_step = self.total_progress / self.number_of_steps
        self.signals = LizardFloodRiskAnalysisSignals()

    def analyze_buildings_flood_risk(self):
        success_statuses = {"SUCCESS"}
        in_progress_statuses = {"PENDING", "UNKNOWN", "STARTED", "RETRY"}
        lizard_url = self.downloader.LIZARD_URL
        api_key = self.downloader.get_api_key()
        # Remove existing buildings results objects from the scenario
        progress_msg = f"Remove existing \"buildings\" results objects from the scenario '{self.scenario_name}'..."
        self.report_progress(progress_msg)
        clean_up_buildings_result(lizard_url, api_key, self.scenario_instance)
        # Create a "buildings" result object for the scenario
        progress_msg = f"Create a \"buildings\" result object for the scenario '{self.scenario_name}'..."
        self.report_progress(progress_msg)
        buildings_result = create_buildings_result(lizard_url, api_key, self.scenario_instance)
        progress_msg = f"Upload buildings for the scenario '{self.scenario_name}'..."
        self.report_progress(progress_msg)
        buildings_result_upload_url = buildings_result["upload_url"]
        upload_local_file(buildings_result_upload_url, self.buildings_gpkg)
        progress_msg = f"Create a \"vulnerable buildings\" result object for the scenario '{self.scenario_name}'..."
        self.report_progress(progress_msg)
        vulnerable_buildings_result = create_vulnerable_buildings_result(lizard_url, api_key, self.scenario_instance)
        result_id = vulnerable_buildings_result["id"]
        progress_msg = f'Spawn processing of the "vulnerable buildings" result task...'
        self.report_progress(progress_msg)
        process_task = create_buildings_flood_risk_task(
            lizard_url, api_key, self.scenario_instance, result_id, self.calculation_method, self.output_format
        )
        process_task_id = process_task["task_id"]
        # Check status of task
        progress_msg = f"Check processing task status..."
        self.report_progress(progress_msg)
        task_processed = False
        while not task_processed:
            task_status = self.downloader.get_task_status(process_task_id)
            if task_status in success_statuses:
                task_processed = True
            elif task_status in in_progress_statuses:
                continue
            else:
                error_msg = f"Task {process_task_id} failed, status was: {task_status}"
                raise LizardFloodRiskAnalysisError(error_msg)
            time.sleep(self.TASK_CHECK_SLEEP_TIME)

    @pyqtSlot()
    def run(self):
        """Run flood risk analysis for buildings."""
        try:
            self.report_progress(increase_current_step=False)
            self.analyze_buildings_flood_risk()
            self.report_finished("Scenario flood risk analysis finished.")
        except LizardFloodRiskAnalysisError as e:
            self.report_failure(str(e))
        except Exception as e:
            error_msg = f"Buildings flood risk analysis failed due to the following error: {e}"
            self.report_failure(error_msg)

    def report_progress(self, progress_message=None, increase_current_step=True):
        """Report worker progress."""
        current_progress = int(self.current_step * self.percentage_per_step)
        if increase_current_step:
            self.current_step += 1
        self.signals.analysis_progress.emit(
            self.scenario_instance, progress_message, current_progress, self.total_progress
        )

    def report_failure(self, error_message):
        """Report worker failure message."""
        self.signals.analysis_failed.emit(self.scenario_instance, error_message)

    def report_finished(self, message):
        """Report worker finished message."""
        self.signals.analysis_finished.emit(self.scenario_instance, message)
