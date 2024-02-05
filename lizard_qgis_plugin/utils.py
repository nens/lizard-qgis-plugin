# Lizard plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
import os
import uuid
from math import ceil, sqrt
from xml.etree import ElementTree

import requests
from osgeo import gdal
from qgis._core import QgsFeature
from qgis.core import (
    QgsApplication,
    QgsAuthMethodConfig,
    QgsCoordinateTransform,
    QgsField,
    QgsGeometry,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSettings, QVariant

LIZARD_SETTINGS_ENTRY = "lizard_qgis_plugin"
LIZARD_AUTHCFG_ENTRY = f"{LIZARD_SETTINGS_ENTRY}/authcfg"


class WMSServiceException(Exception):
    pass


def get_api_key_authcfg_id():
    """Getting Lizard credentials ID from the QGIS Authorization Manager."""
    settings = QSettings()
    authcfg_id = settings.value(LIZARD_AUTHCFG_ENTRY, None)
    return authcfg_id


def get_api_key_auth_manager():
    """Getting Lizard credentials from the QGIS Authorization Manager."""
    authcfg_id = get_api_key_authcfg_id()
    auth_manager = QgsApplication.authManager()
    authcfg = QgsAuthMethodConfig()
    auth_manager.loadAuthenticationConfig(authcfg_id, authcfg, True)
    api_key = authcfg.config("password")
    return api_key


def set_api_key_auth_manager(api_key):
    """Setting Lizard credentials in the QGIS Authorization Manager."""
    username = "__key__"
    settings = QSettings()
    authcfg_id = settings.value(LIZARD_AUTHCFG_ENTRY, None)
    authcfg = QgsAuthMethodConfig()
    auth_manager = QgsApplication.authManager()
    auth_manager.setMasterPassword()
    auth_manager.loadAuthenticationConfig(authcfg_id, authcfg, True)
    if authcfg.id():
        authcfg.setConfig("username", username)
        authcfg.setConfig("password", api_key)
        auth_manager.updateAuthenticationConfig(authcfg)
    else:
        authcfg.setMethod("Basic")
        authcfg.setName("Lizard Api Key")
        authcfg.setConfig("username", username)
        authcfg.setConfig("password", api_key)
        auth_manager.storeAuthenticationConfig(authcfg)
        settings.setValue(LIZARD_AUTHCFG_ENTRY, authcfg.id())


def get_capabilities_layer_uris(wms_url):
    """Get WMS layer URIs."""
    get_capabilities_response = requests.get(url=wms_url, auth=("__key__", get_api_key_auth_manager()))
    get_capabilities_xml = get_capabilities_response.text
    root = ElementTree.fromstring(get_capabilities_xml)
    namespace = root.tag.replace("WMS_Capabilities", "")
    layer_tag = f"{namespace}Layer"
    name_tag = f"{namespace}Name"
    title_tag = f"{namespace}Title"
    crs_tag = f"{namespace}CRS"
    dimension_tag = f"{namespace}Dimension"
    style_tag = f"{namespace}Style"
    layer_section_elements = list(root.iter(layer_tag))
    if not layer_section_elements:
        exception_namespace = root.tag.replace("ServiceExceptionReport", "")
        exception_details_tag = f"{exception_namespace}ServiceException"
        exception_details = next(root.iter(exception_details_tag), "Exception details not found")
        exception_details_text = exception_details.text.replace("detail:", "").strip()
        raise WMSServiceException(exception_details_text)
    layer_group, layer_elements = layer_section_elements[0], layer_section_elements[1:]
    wms_uris = []
    authcfg_id = get_api_key_authcfg_id()
    authcfg_parameter = f"authcfg={authcfg_id}" if authcfg_id else None
    url_parameter = f"url={wms_url}"
    for layer_element in layer_elements:
        layer_wms_parameters = [url_parameter]
        layer_name_element = next(layer_element.iter(name_tag), None)
        layer_title_element = next(layer_element.iter(title_tag), None)
        layer_crs_element = next(layer_element.iter(crs_tag))
        layer_dimension_element = next(layer_element.iter(dimension_tag), None)
        layer_style_element = next(layer_element.iter(style_tag), None)
        layer_title = layer_title_element.text
        layer_name = layer_name_element.text
        layer_wms_parameters.append(f"layers={layer_name}")
        layer_crs = layer_crs_element.text
        layer_wms_parameters.append(f"crs={layer_crs}")
        if layer_dimension_element is not None and layer_dimension_element.attrib["name"] == "time":
            time_dimension_extent = layer_dimension_element.text.strip()
            layer_wms_parameters += [
                "allowTemporalUpdates=true",
                "type=wmst",
                f"timeDimensionExtent={time_dimension_extent}",
            ]
        if layer_style_element is not None:
            layer_wms_parameters.append("styles")
        if authcfg_parameter:
            layer_wms_parameters.append(authcfg_parameter)
        layer_wms_parameters.sort()
        layer_uri = "&".join(layer_wms_parameters)
        wms_uris.append((layer_title, layer_uri))
    return wms_uris


def get_available_rasters_list(lizard_url):
    """List all available rasters."""
    url = f"{lizard_url}rasters/"
    r = requests.get(url=url, auth=("__key__", get_api_key_auth_manager()))
    r.raise_for_status()
    response_json = r.json()
    available_rasters = response_json["results"]
    return available_rasters


def find_rasters(lizard_url, limit, **kwargs):
    """Find all available rasters matching given criteria."""
    url = f"{lizard_url}rasters/"
    payload = {"limit": limit}
    payload.update(kwargs)
    r = requests.get(url=url, auth=("__key__", get_api_key_auth_manager()), params=payload)
    r.raise_for_status()
    response_json = r.json()
    matching_rasters = response_json["results"]
    return matching_rasters


def count_rasters_with_name(lizard_url, name):
    """Return rasters search results count."""
    url = f"{lizard_url}rasters/"
    payload = {"name__icontains": name, "limit": 1}
    r = requests.get(url=url, auth=("__key__", get_api_key_auth_manager()), params=payload)
    r.raise_for_status()
    response_json = r.json()
    results_count = response_json["count"]
    return results_count


def count_scenarios_with_name(lizard_url, name):
    """Return scenario search results count."""
    url = f"{lizard_url}scenarios/"
    payload = {"name__icontains": name, "limit": 1}
    r = requests.get(url=url, auth=("__key__", get_api_key_auth_manager()), params=payload)
    r.raise_for_status()
    response_json = r.json()
    results_count = response_json["count"]
    return results_count


def create_tree_group(name, insert_at_top=True):
    """Creating layer tree group with given name."""
    root = QgsProject.instance().layerTreeRoot()
    grp = QgsLayerTreeGroup(name)
    root.insertChildNode(0 if insert_at_top else -1, grp)
    return grp


def add_layer_to_group(group, layer, insert_at_top=False):
    """Adding layer to the specific group."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    project.addMapLayer(layer, False)
    group.insertChildNode(0 if insert_at_top else -1, QgsLayerTreeLayer(layer))
    layer_node = root.findLayer(layer.id())
    layer_node.setExpanded(False)


def try_to_write(working_dir):
    """Try to write and remove an empty text file into given location."""
    test_filename = f"{uuid.uuid4()}.txt"
    test_file_path = os.path.join(working_dir, test_filename)
    with open(test_file_path, "w") as test_file:
        test_file.write("")
    os.remove(test_file_path)


def split_scenario_extent(scenario_instance, max_pixel_count=1 * 10**8):
    """
    Split raster task spatial bounds to fit in to maximum pixel count limit.
    Reimplemented code from https://github.com/nens/threedi-scenario-downloader
    """
    x1 = scenario_instance["origin_x"]
    y1 = scenario_instance["origin_y"]
    x2 = scenario_instance["upper_bound_x"]
    y2 = scenario_instance["upper_bound_y"]
    pixelsize_x = abs(scenario_instance["pixelsize_x"])
    pixelsize_y = abs(scenario_instance["pixelsize_y"])
    width = abs((x2 - x1) / pixelsize_x)
    height = abs((y2 - y1) / pixelsize_y)
    if not width.is_integer():
        width = ceil(width)
        x2 = (width * pixelsize_x) + x1
    if not height.is_integer():
        height = ceil(height)
        y2 = (height * pixelsize_y) + y1
    raster_pixel_count = width * height
    if raster_pixel_count > max_pixel_count:
        max_pixel_per_axis = int(sqrt(max_pixel_count))
        columns_count = ceil(width / max_pixel_per_axis)
        rows_count = ceil(height / max_pixel_per_axis)
        sub_width = max_pixel_per_axis * pixelsize_x
        sub_height = max_pixel_per_axis * pixelsize_y
        bboxes = []
        for column_idx in range(columns_count):
            sub_x1 = x1 + (column_idx * sub_width)
            sub_x2 = sub_x1 + sub_width
            for row_idx in range(rows_count):
                sub_y1 = y1 + (row_idx * sub_height)
                sub_y2 = sub_y1 + sub_height
                sub_bbox = (sub_x1, sub_y1, sub_x2, sub_y2)
                bboxes.append(sub_bbox)
        spatial_bounds = (bboxes, sub_width, sub_height)
    else:
        bboxes = [(x1, y1, x2, y2)]
        spatial_bounds = (bboxes, width, height)
    return spatial_bounds


def split_raster_extent(raster_instance, bbox, resolution=None, max_pixel_count=1 * 10**8):
    """Split raster task spatial bounds to fit in to maximum pixel count limit."""
    x1_src = raster_instance["origin_x"]
    y1_src = raster_instance["origin_y"]
    x2_src = raster_instance["upper_bound_x"]
    y2_src = raster_instance["upper_bound_y"]
    x1, y1, x2, y2 = bbox
    if x1_src > x1:
        x1 = x1_src
    if x2_src < x2:
        x2 = x2_src
    if y1_src > y1:
        y1 = y1_src
    if y2_src < y2:
        y2 = y2_src
    if resolution is None:
        pixelsize_x = abs(raster_instance["pixelsize_x"])
        pixelsize_y = abs(raster_instance["pixelsize_y"])
    else:
        pixelsize_x = resolution
        pixelsize_y = resolution
    width = abs((x2 - x1) / pixelsize_x)
    height = abs((y2 - y1) / pixelsize_y)
    if not width.is_integer():
        width = ceil(width)
        x2 = (width * pixelsize_x) + x1
    if not height.is_integer():
        height = ceil(height)
        y2 = (height * pixelsize_y) + y1
    raster_pixel_count = width * height
    if raster_pixel_count > max_pixel_count:
        max_pixel_per_axis = int(sqrt(max_pixel_count))
        columns_count = ceil(width / max_pixel_per_axis)
        rows_count = ceil(height / max_pixel_per_axis)
        sub_width = max_pixel_per_axis * pixelsize_x
        sub_height = max_pixel_per_axis * pixelsize_y
        bboxes = []
        for column_idx in range(columns_count):
            sub_x1 = x1 + (column_idx * sub_width)
            sub_x2 = sub_x1 + sub_width
            for row_idx in range(rows_count):
                sub_y1 = y1 + (row_idx * sub_height)
                sub_y2 = sub_y1 + sub_height
                sub_bbox = (sub_x1, sub_y1, sub_x2, sub_y2)
                bboxes.append(sub_bbox)
        spatial_bounds = (bboxes, sub_width, sub_height)
    else:
        bboxes = [(x1, y1, x2, y2)]
        spatial_bounds = (bboxes, width, height)
    return spatial_bounds


def get_url_raster_instance(api_key, raster_url):
    """Return raster instance from the raster URL."""
    r = requests.get(
        url=raster_url,
        auth=("__key__", api_key),
    )
    r.raise_for_status()

    raster = r.json()
    return raster


def create_raster_tasks(lizard_url, api_key, raster, spatial_bounds, projection=None, no_data=None, start_time=None):
    """
    Create Lizard raster task.
    Reimplemented code from https://github.com/nens/threedi-scenario-downloader
    """
    raster_id = raster["uuid"]
    url = f"{lizard_url}rasters/{raster_id}/data/"
    bboxes, width, height = spatial_bounds
    raster_tasks = []
    for x1, y1, x2, y2 in bboxes:
        bbox = f"{x1},{y1},{x2},{y2}"
        payload = {
            "width": width,
            "height": height,
            "bbox": bbox,
            "projection": projection,
            "format": "geotiff",
            "async": "true",
        }
        if no_data is not None:
            payload["nodata"] = no_data
        if start_time is not None:
            payload["start"] = start_time
        r = requests.get(url=url, auth=("__key__", api_key), params=payload)
        r.raise_for_status()
        raster_task = r.json()
        raster_tasks.append(raster_task)
    return raster_tasks


def build_vrt(output_filepath, raster_filepaths, **vrt_options):
    """Build VRT for the list of rasters."""
    options = gdal.BuildVRTOptions(**vrt_options)
    vrt_ds = gdal.BuildVRT(output_filepath, raster_filepaths, options=options)
    vrt_ds = None


def reproject_geometry(geometry, src_crs, dst_crs, transformation=None):
    """Reproject geometry from source CRS to destination CRS."""
    if src_crs == dst_crs:
        return geometry
    if transformation is None:
        project = QgsProject.instance()
        transform_context = project.transformContext()
        transformation = QgsCoordinateTransform(src_crs, dst_crs, transform_context)
    geometry.transform(transformation)
    return geometry


def wkt_polygon_layer(polygon_wkt, polygon_layer_name="clip_layer", epsg="EPSG:4326"):
    """Spawn (multi)polygon layer out of single WKT polygon geometry."""
    geometry_type = "MultiPolygon" if polygon_wkt.lower().startswith("multi") else "Polygon"
    uri = f"{geometry_type}?crs={epsg}"
    memory_polygon_layer = QgsVectorLayer(uri, polygon_layer_name, "memory")
    memory_polygon_layer_dt = memory_polygon_layer.dataProvider()
    memory_polygon_layer_dt.addAttributes([QgsField("id", QVariant.Int)])
    memory_polygon_layer.updateFields()
    cut_line_feat = QgsFeature(memory_polygon_layer.fields())
    cut_line_feat.setGeometry(QgsGeometry.fromWkt(polygon_wkt))
    memory_polygon_layer.startEditing()
    memory_polygon_layer.addFeature(cut_line_feat)
    memory_polygon_layer.commitChanges()
    return memory_polygon_layer


def layer_to_gpkg(layer, gpkg_filename, overwrite=False, driver_name="GPKG"):
    """Function which saves memory layer into GeoPackage file."""
    transform_context = QgsProject.instance().transformContext()
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.actionOnExistingFile = (
        QgsVectorFileWriter.CreateOrOverwriteLayer if overwrite is False else QgsVectorFileWriter.CreateOrOverwriteFile
    )
    fields = layer.fields()
    valid_indexes = [fields.lookupField(fname) for fname in fields.names() if fname != "fid"]
    options.attributes = valid_indexes
    options.driverName = driver_name
    options.layerName = layer.name()
    writer, error = QgsVectorFileWriter.writeAsVectorFormatV2(layer, gpkg_filename, transform_context, options)
    return writer, error


def clip_raster(raster_src, polygon_clip_gpkg, polygon_clip_layer="clip_layer", no_data=-9999):
    """Clip raster with given polygon geometry."""
    warp_options = gdal.WarpOptions(
        dstNodata=no_data,
        cutlineDSName=polygon_clip_gpkg,
        cutlineLayer=polygon_clip_layer,
        cropToCutline=True,
        multithread=True,
        warpOptions=["NUM_THREADS=ALL_CPUS"],
    )
    raster_location = os.path.dirname(raster_src)
    raster_filename = os.path.basename(raster_src)
    raster_dst = os.path.join(raster_location, f"clip_{raster_filename}")
    gdal.Warp(raster_dst, raster_src, options=warp_options)
    os.remove(raster_src)
    os.rename(raster_dst, raster_src)
