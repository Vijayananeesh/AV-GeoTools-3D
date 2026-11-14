# AV GeoTools 3D

QGIS plugin by Aneesh Vijayan for interactive 3D visualisation of soil and groundwater investigation data.

## What it does
- Boreholes with lithology, a DEM (with an optional satellite or aerial image draped on it), groundwater surface and contours.
- Geological models, fence diagrams and cut-away views.
- Soil plumes (one depth per sample) and groundwater plumes (screened intervals): kriging or inverse-distance interpolation drawn as nested 3D shells, limited by the ground, the water table, the bottom of the wells and the area of the wells.
- Plume volume above a guideline value, in total and per lithology unit (groundwater also as water volume from the porosity).
- Editable text boxes, legends, export of the model as a stand-alone interactive web page (HTML).
- "Prepare data" tool: turns Excel / CSV tables into QGIS layers.
- Demo data (20 wells with soil and groundwater results and a synthetic satellite image) to try everything.

## Requirements
QGIS 4.x (Windows installer) with its Python modules VTK, numpy and scipy. Tested on QGIS 4.2.2.

If the plugin says that the Python package `vtk` (or `scipy`, `numpy`) is missing: close QGIS, open the **OSGeo4W Shell** (Start menu > QGIS), run `python -m pip install vtk` (use `--user` if you have no administrator rights), and start QGIS again.

## Install
Plugins > Manage and Install Plugins > Install from ZIP, choose the ZIP file, then open it from the Plugins menu ("AV GeoTools 3D") or the toolbar button.
Tools > Demo data adds example layers.

## Notes
- The built-in demo data and the demo satellite image are synthetic.
- If you drape online imagery (Esri, Google, Bing ...), the imagery provider's terms of use apply.
- Volumes are estimates from an interpolated plume; they are only as good as the samples behind them.

## About the development
Developed by Aneesh Vijayan, who designed the features and workflows, supplied the site-investigation know-how and tested the plugin. The code was written with the help of AI (Claude, by Anthropic). Version 0.1 has been tested on QGIS 4.2.2 with the built-in demo data; please report problems in the Issues tab.

## Licence
GNU General Public License, version 2 or later. See LICENSE.

## Contact
Aneesh Vijayan, krishnanvijayananeesh@gmail.com


## Reporting a problem
When something goes wrong, the message window has a **Report this problem by e-mail** button (and **Copy details**). It opens an e-mail to the author with the error text and the program versions; your Windows user name is removed from file paths. Nothing is sent until you press Send in your own mail program.
