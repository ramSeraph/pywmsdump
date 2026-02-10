from enum import Enum


class Service(Enum):
    WMS = "WMS"
    WFS = "WFS"

class WMSVersion(Enum):
    V1_1_1 = "1.1.1"
    V1_3_0 = "1.3.0"

class WFSVersion(Enum):
    V1_0_0 = "1.0.0"
    V1_1_0 = "1.1.0"
    V2_0_0 = "2.0.0"

class Operation(Enum):
    GetMap = "GetMap"
    GetFeatureInfo = "GetFeatureInfo"
    GetFeature = "GetFeature"

class RetrievalMode(Enum):
    OFFSET = "OFFSET"
    EXTENT = "EXTENT"
    EXTENT_FIXED_BUFFER = "EXTENT_FIXED_BUFFER"

class Format(Enum):
    GEORSS = "GEORSS"
    KML = "KML"

class Flavor(Enum):
    Geoserver = "Geoserver"
    QGISserver = "QGISserver"

class Extent(Enum):
    NOT_PRESENT = 0
    OPEN = 1
    EXPLORED = 2

def parse_version(service, version_str):
    """Convert a version string to the appropriate version enum based on service type."""
    if service == Service.WMS:
        return WMSVersion(version_str)
    return WFSVersion(version_str)
