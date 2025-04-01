# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
import sys

MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
REQUIRED_SCENARIO_DOWNLOADER_VERSION = "1.4"
REQUIRED_3DI_MI_UTILS_VERSION = "0.1.9"
REQUIRED_SQLALCHEMY_VERSION = "2.0.6"
REQUIRED_GEOALCHEMY2_VERSION = "0.15.2"
REQUIRED_ALEMBIC_VERSION = "1.14.1"
REQUIRED_MAKO_VERSION = "1.3.9"

SCENARIO_DOWNLOADER_WHEEL = os.path.join(
    MAIN_DIR, f"threedi_scenario_downloader-{REQUIRED_SCENARIO_DOWNLOADER_VERSION}-py3-none-any.whl"
)
MI_UTILS_WHEEL = os.path.join(MAIN_DIR, f"threedi_mi_utils-{REQUIRED_3DI_MI_UTILS_VERSION}-py3-none-any.whl")
SQLALCHEMY_WHEEL = os.path.join(MAIN_DIR, f"SQLAlchemy-{REQUIRED_SQLALCHEMY_VERSION}-py3-none-any.whl")
GEOALCHEMY2_WHEEL = os.path.join(MAIN_DIR, f"GeoAlchemy2-{REQUIRED_GEOALCHEMY2_VERSION}-py3-none-any.whl")
ALEMBIC_WHEEL = os.path.join(MAIN_DIR, f"alembic-{REQUIRED_ALEMBIC_VERSION}-py3-none-any.whl")
MAKO_WHEEL = os.path.join(MAIN_DIR, f"Mako-{REQUIRED_MAKO_VERSION}-py3-none-any.whl")



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
        import sqlalchemy
    except ImportError:
        deps_path = SQLALCHEMY_WHEEL
        sys.path.append(deps_path)

    try:
        import geoalchemy2
    except ImportError:
        deps_path = GEOALCHEMY2_WHEEL
        sys.path.append(deps_path)

    try:
        import alembic
    except ImportError:
        deps_path = ALEMBIC_WHEEL
        sys.path.append(deps_path)

    try:
        import threedi_schema
    except ImportError:
        # We no longer directly use the wheels as this caused issues with Alembic and temp files. That's
        # why we add the deps folder (containing threedi_schema) to the path.
        sys.path.append(MAIN_DIR)

    try:
        import threedi_mi_utils
    except ImportError:
        deps_path = MI_UTILS_WHEEL
        sys.path.append(deps_path)

    try:
        import mako
    except ImportError:
        deps_path = MAKO_WHEEL
        sys.path.append(deps_path)
