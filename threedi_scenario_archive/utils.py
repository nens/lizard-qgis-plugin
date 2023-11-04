# 3Di Scenario Archive plugin for QGIS, licensed under GPLv2 or (at your option) any later version
# Copyright (C) 2023 by Lutra Consulting for 3Di Water Management
from xml.etree import ElementTree

import requests
from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsLayerTreeGroup, QgsLayerTreeLayer, QgsProject
from qgis.PyQt.QtCore import QSettings


class WMSServiceException(Exception):
    pass


def get_api_key_authcfg_id():
    """Getting 3Di Scenario Archive credentials ID from the QGIS Authorization Manager."""
    settings = QSettings()
    authcfg_id = settings.value("threedi_scenario_archive/authcfg", None)
    return authcfg_id


def get_api_key_auth_manager():
    """Getting 3Di Scenario Archive credentials from the QGIS Authorization Manager."""
    authcfg_id = get_api_key_authcfg_id()
    auth_manager = QgsApplication.authManager()
    authcfg = QgsAuthMethodConfig()
    auth_manager.loadAuthenticationConfig(authcfg_id, authcfg, True)
    api_key = authcfg.config("password")
    return api_key


def set_api_key_auth_manager(api_key):
    """Setting 3Di Scenario Archive credentials in the QGIS Authorization Manager."""
    username = "__key__"
    settings = QSettings()
    authcfg_id = settings.value("threedi_scenario_archive/authcfg", None)
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
        settings.setValue("threedi_scenario_archive/authcfg", authcfg.id())


def get_capabilities_layer_uris(wms_url):
    """Get WMS layer URIs."""
    get_capabilities_xml = requests.get(url=wms_url, auth=("__key__", get_api_key_auth_manager())).text
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
