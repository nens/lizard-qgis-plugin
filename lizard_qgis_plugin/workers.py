# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
import time

import requests
from qgis.PyQt.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot
from threedi_mi_utils import bypass_max_path_limit


class ScenarioDownloadError(Exception):
    """Scenario files downloader exception class."""

    pass


class ScenarioItemsDownloaderSignals(QObject):
    """Definition of the download worker signals."""

    download_progress = pyqtSignal(dict, str, int, int)
    download_finished = pyqtSignal(str)
    download_failed = pyqtSignal(str)


class ScenarioItemsDownloader(QRunnable):
    """Worker object responsible for downloading scenario files."""

    def __init__(self, downloader, scenario_instance, raw_results_to_download, raster_results, download_dir):
        super().__init__()
        self.downloader = downloader
        self.scenario_instance = scenario_instance
        self.scenario_id = scenario_instance["uuid"]
        self.raw_results_to_download = raw_results_to_download
        self.raster_results = raster_results
        self.scenario_download_dir = os.path.join(download_dir, f"scenario_{self.scenario_id}")
        self.total_progress = 100
        self.current_step = 0
        self.number_of_steps = len(self.raw_results_to_download) + len(self.raster_results) + 1  # Extra task step
        self.percentage_per_step = self.total_progress / self.number_of_steps
        self.signals = ScenarioItemsDownloaderSignals()

    def download_raw_results(self):
        for result in self.raw_results_to_download:
            attachment_url = result["attachment_url"]
            attachment_filename = result["filename"]
            target_filepath = bypass_max_path_limit(os.path.join(self.scenario_download_dir, attachment_filename))
            progress_msg = f"Downloading '{attachment_filename}' (scenario ID: '{self.scenario_id}')..."
            self.report_progress(progress_msg)
            try:
                self.downloader.download_file(attachment_url, target_filepath)
            except Exception as e:
                error_msg = f"Download of the {attachment_filename} failed due to the following error: {e}"
                raise ScenarioDownloadError(error_msg)

    def download_raster_results(self):
        task_raster_results, processed_tasks = {}, {}
        success_statuses = {"SUCCESS"}
        in_progress_statuses = {"PENDING", "UNKNOWN", "STARTED", "RETRY"}
        # Create tasks
        progress_msg = f"Spawning raster tasks and preparing for download (scenario ID '{self.scenario_id}')..."
        self.report_progress(progress_msg)
        for raster_result in self.raster_results:
            raster_url = raster_result["raster"]
            raster_response = requests.get(
                url=raster_url,
                auth=("__key__", self.downloader.get_api_key()),
            )
            raster_response.raise_for_status()
            raster = raster_response.json()
            # TODO: Add optional parameters for raster task creation
            task = self.downloader.create_raster_task(
                raster,
                self.scenario_instance,
            )
            task_id = task["task_id"]
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
                    raise ScenarioDownloadError(error_msg)
            time.sleep(5)
        # Download tasks files
        for task_id, raster_result in task_raster_results.items():
            raster_filename = raster_result["filename"]
            progress_msg = f"Downloading '{raster_filename}' (scenario ID: '{self.scenario_id}')..."
            self.report_progress(progress_msg)
            raster_filepath = bypass_max_path_limit(os.path.join(self.scenario_download_dir, raster_filename))
            self.downloader.download_task(task_id, raster_filepath)

    @pyqtSlot()
    def run(self):
        """Downloading simulation results files."""
        try:
            self.report_progress(increase_current_step=False)
            if not os.path.exists(self.scenario_download_dir):
                os.makedirs(self.scenario_download_dir)
            self.download_raw_results()
            self.download_raster_results()
            self.report_finished("Scenario items download finished.")
        except ScenarioDownloadError as e:
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
        self.signals.download_failed.emit(error_message)

    def report_finished(self, message):
        """Report worker finished message."""
        self.signals.download_finished.emit(message)
