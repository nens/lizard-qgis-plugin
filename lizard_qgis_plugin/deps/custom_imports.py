# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
import sys

MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
REQUIRED_SCENARIO_DOWNLOADER_VERSION = "1.4"
REQUIRED_3DI_MI_UTILS_VERSION = "0.1.2"
SCENARIO_DOWNLOADER_WHEEL = os.path.join(
    MAIN_DIR, f"threedi_scenario_downloader-{REQUIRED_SCENARIO_DOWNLOADER_VERSION}-py3-none-any.whl"
)
MI_UTILS_WHEEL = os.path.join(MAIN_DIR, f"threedi_mi_utils-{REQUIRED_3DI_MI_UTILS_VERSION}-py3-none-any.whl")


def patch_wheel_imports():
    """
    Function that tests if extra modules are installed.
    If modules are not available then it will add missing modules wheels to the Python path.
    """
    try:
        import threedi_scenario_downloader
    except ImportError:
        deps_path = SCENARIO_DOWNLOADER_WHEEL
        sys.path.append(deps_path)

    try:
        import threedi_mi_utils
    except ImportError:
        deps_path = MI_UTILS_WHEEL
        sys.path.append(deps_path)
