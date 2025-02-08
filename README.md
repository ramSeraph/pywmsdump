# wmsdump [![PyPI - Latest Version](https://img.shields.io/pypi/v/wmsdump)](https://pypi.org/project/wmsdump/) [![GitHub Tag](https://img.shields.io/github/v/tag/ramSeraph/pywmsdump?filter=v*)](https://github.com/ramSeraph/pywmsdump/releases/latest)

A library and command-line tool for extracting data from OGC services (WMS, WFS).

## Features

*   **Supports WMS and WFS:** Extracts data from both Web Map Service (WMS) and Web Feature Service (WFS) endpoints.
*   **Flexible Retrieval Modes:** Offers `OFFSET` (paged retrieval) and `EXTENT` (bbox splitting and drilling down by spatial extent) retrieval modes for efficient data extraction, including handling deduplication with the EXTENT mode.
*   **Multiple Retrieval Formats:** Supports KML and GeoRSS formats when retrieving data from WMS GetMap operations. Output is always in Geojsonl(GeoJSONSeq) 
*   **Geometry Precision Control:** Allows truncating geometry coordinates to a specified decimal point precision.
*   **State Management:**  Persists extraction state to allow resuming interrupted downloads.
*   **Geoserver and QGIS Server Flavor Support:** Handles vendor-specific differences for GetFeatureInfo based retrieval from WMS.
*   **Error Handling:** Provides informative error messages and handles common service exceptions.
*   **Configuration:** Customizable through command-line options.
*   **KML Postprocessing:** Offers options to strip superflous points in Polygon/LineString geometry collections and whether to keep original style related props.
*   **Hole Punching:** Includes a utility to remove overlap in polygons by punching holes to deal with shortcomings of GeoRSS based retrieval 
*   **Capabilities Exploration:**  Can explore services via a GetCapabilities request or by scraping the Geoserver webpage. Partial parsing of incomplete/corrupt capabilitie.xml response is supported

## Installation

1.  **Using `pip`:**

    ```bash
    pip install wmsdump
    ```

2.  **Using `uv` (recommended):**

    `wmsdump` uses `uv` for package management and dependency resolution. `uv` is a faster alternative to `pip`.
    
    Installing uv - https://docs.astral.sh/uv/getting-started/installation

    ```bash
    # Install dependencies using uv
    uv pip install wmsdump
    ```

    You can also use the tools directly by running
    ```bash
    uvx --from wmsdump wms-extractor <args>
    ```

    uv creates a temporary virtualenv and manages your dependencies in this invocation.


    For the optional `punch-holes` feature( needed for using the punch-holes utility ), use:

    ```bash
    uv pip install wmsdump[punch-holes]
    ```

    or

    ```bash
    pip install wmsdump[punch-holes]
    ```

    For the optional `proj` feature( needed for retrieving data in projections other than EPSG:4326 or EPSG:3857 ), use:

    ```bash
    uv pip install wmsdump[proj]
    ```

    or

    ```bash
    pip install wmsdump[proj]
    ```

## Usage

`wmsdump` provides a command-line tool `wms-extractor` with two main commands: `explore` and `extract`.

### Common Options

```bash
wms-extractor --help
```

*    `--log-level`: Log level. One of DEBUG,INFO,WARNING,ERROR,CRITICAL. Defaults to INFO.
*    `--no-ssl-verify`: switch off ssl verification for all network calls.
*    `--request-timeout`: timeout for the http requests in seconds. Default is no timeout.
*    `--header`: Header to be added to all network requests, in the format "Key:Value". Can be used multiple times.

### 1. Explore

The `explore` command helps discover available layers and service information.

```bash
wms-extractor explore --help
```

**Options:**

*   `--geoserver-url`: URL of the GeoServer endpoint.  The WMS endpoint is assumed to be `<geoserver_url>/ows`.
*   `--service-url`: URL of the WMS/WFS endpoint from which to probe for capabilities. If not provided, it will be derived from `geoserver-url`.
*   `--service`: Service to use (WMS or WFS). Defaults to WFS.
*   `--service-version`: The protocol version to use. Defaults to '1.1.1' for WMS and '1.0.0' for WFS.
*   `--namespace`: Only look for layers in a given namespace (Geoserver specific).
*   `--output-file`: File to write the layer list to.
*   `--scrape-webpage`: Scrape the GeoServer web page instead of reading capabilities. Useful when capabilities are broken.

**Examples:**

```bash
# Explore WFS layers from a GeoServer endpoint
wms-extractor explore --geoserver-url http://example.com/geoserver

# Explore WMS layers from a specific URL
wms-extractor explore --service-url http://example.com/wms --service WMS

# Scrape the GeoServer web page for layers
wms-extractor explore --geoserver-url http://example.com/geoserver --scrape-webpage

# Write layer list to a file
wms-extractor explore --geoserver-url http://example.com/geoserver --output-file layers.txt
```

### 2. Extract

The `extract` command extracts data from a specified layer.

```bash
wms-extractor extract --help
```

**Arguments:**

*   `LAYERNAME`: Name of the layer to extract.
*   `OUTPUT_FILE`: Output file to write the GeoJSONl features to.  If not provided, a filename will be derived from the LAYERNAME.

**Options:**

*   `--output-dir`: Directory to write output files in (only used when `OUTPUT_FILE` is not given). Defaults to the current directory.
*   `--geoserver-url`: URL of the GeoServer endpoint. `service-url` is assumed to be `<geoserver_url>/[<layer_namespace>/]ows`.
*   `--service-url`: URL of the WMS/WFS endpoint from which to retrieve data. If not provided, it will be derived from `geoserver-url`.
*   `--service`: Service to use (WMS or WFS). Defaults to WFS.
*   `--service-version`: The protocol version to use. Defaults to '1.1.1' for WMS and '1.0.0' for WFS.
*   `--retrieval-mode`: Which method to use for batch record retrieval (`OFFSET` or `EXTENT`). Defaults to `OFFSET`.
*   `--operation`: Which operation to use for querying a WMS endpoint (`GetMap` or `GetFeatureInfo`). Defaults to `GetMap`.
*   `--flavor`: Vendor of the WMS service (`Geoserver` or `QGISserver`), useful to specify for GetFeatureInfo based retrieval. Defaults to `Geoserver`.
*   `--sort-key`: Key to use for paged retrieval (required when server requires it).
*   `--batch-size`: Batch size to use for retrieval. Defaults to 1000.
*   `--pause-seconds`: Amount of time to pause between a batch of requests. Defaults to 2.
*   `--requests-to-pause`: Number of requests to make before pausing. Defaults to 10.
*   `--max-attempts`: Number of times to attempt a request before giving up. Defaults to 5.
*   `--retry-delay`: Number of seconds to wait before retrying on failure (delay is incremented for each failure). Defaults to 5.
*   `--geometry-precision`: Decimal point precision of geometry to be returned (-1 means no truncation). Defaults to -1.
*   `--getmap-format`: Format to use while pulling using WMS GetMap (`KML` or `GEORSS`). Defaults to `KML`.
*   `--kml-strip-point`: Whether to strip the points in polygons and linestring geomcollections (KML specific). Defaults to `True`.
*   `--kml-keep-original-props`: Whether to keep the original style-related properties in KML conversion. Defaults to `False`.
*   `--out-srs`: CRS to request data in. Defaults to `EPSG:4326`.
*   `--bounds`: Bounding box to restrict the query to (format: `<xmin>,<ymin>,<xmax>,<ymax>`).
*   `--max-box-dims`: When querying using EXTENT mode, the maximum size of the bounding box to use (format: `<deltax>,<deltay>`).
*   `--skip-index`: Skip n elements in index (useful to skip records causing failure, only applicable for OFFSET retrieval).  Defaults to 0.

**Examples:**

```bash
# Extract data from a WFS layer
wms-extractor extract my_layer output.geojsonl --geoserver-url http://example.com/geoserver

# Extract data from a WMS layer using GetMap with GeoRSS format
wms-extractor extract my_layer output.geojsonl --service WMS --service-url http://example.com/wms --getmap-format GEORSS

# Extract data and truncate geometry to 3 decimal places
wms-extractor extract my_layer output.geojsonl --geoserver-url http://example.com/geoserver --geometry-precision 3

# Extract data with bounding box
wms-extractor extract my_layer output.geojsonl --geoserver-url http://example.com/geoserver --bounds -180,-90,180,90
```

### 3. Punch Holes (Optional)

This command is available if installed with the `punch-holes` extra.  It removes overlaps in a GeoJSONl file by punching holes where polygons overlap.  This is useful for cleaning up data problems which happen when extracting data using GeoRSS format which cannot represent polygons with holes.

```bash
punch-holes --help
```

**Arguments:**

*   `INPUT_FILE`: The input GeoJSONl file to process
*   `OUTPUT_FILE`: The output GeoJSONl file. If none provided, writes the results to `fixed_<INPUT_FILE>`

**Options:**

*   `--index-in-mem`: Whether the spatial index keeps the geometry data in memory or just the offset of the features on disk.
*   `--keep-map-file`:  Whether to keep the overlap map temporary file (debugging purposes).

**Example:**

```bash
punch-holes input.geojsonl output.geojsonl
```

## State Management

`wmsdump` automatically creates a `.state` file alongside the output file. This file stores the progress of the extraction. If the extraction is interrupted, `wmsdump` will resume from the last known state when run again with the same parameters. To start a new extraction, delete both the output file and the `.state` file.

## Environment Variables

*   `WMSDUMP_SAVE_RESPONSE_TO_FILE`: If set, the raw HTTP response from the OGC service will be saved to the specified file. This is useful for debugging.

## Dependencies

*   `bs4` (Beautiful Soup 4)
*   `click`
*   `colorlog`
*   `jsonschema`
*   `kml2geojson`
*   `requests`
*   `xmltodict`

**Optional:**

*   `geoindex-rs` (required for `punch-holes`)
*   `numpy` (required for `punch-holes`)
*   `shapely` (required for `punch-holes`)
*   `pyproj` (required for handling some CRS definitions)

## Contributing

Contributions are welcome! Please submit bug reports, feature requests, and pull requests through GitHub.

## License

This project is released under UnLicense - see the `LICENSE` file for details.

## Credits

This was heavily inspired by a similar tool for ESRI endpoints - [openaddresses/pyesridump](https://github.com/openaddresses/pyesridump)

Also, that this is possible was pointed out to me by [datta07](https://github.com/datta07), some of the georss parsing code was also based on prior work by [datt07](https://github.com/datta07), [answerquest](https://github.com/answerquest) and [devdattaT](https://github.com/devdattaT).

