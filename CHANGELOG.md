# Changelog

All notable changes to **AV GeoTools 3D** (QGIS 4, Windows). Newest first.

## 0.4.9
- Internal clean-up: problems are now written to the QGIS log instead of being silently ignored.

## 0.4.8
- Changed: removed the deprecated `supportsQt6` flag from the metadata; QGIS 4 compatibility is set by `qgisMaximumVersion=4.99`.

## 0.4.7 - First stable release
- Changed: no longer marked experimental, so it appears in the QGIS Plugin Manager without extra settings.
- Note: needs the Python package `vtk`. If it is missing, close QGIS, open the OSGeo4W Shell and run `python -m pip install vtk`.

## 0.4.6
- Added: **Report this problem by e-mail** and **Copy details** buttons on error messages (the Windows user name is removed, the program versions are included).

## 0.4.5
- Fixed: the **Cloud** plume display failed with a NameError; the cloud routine is restored (soil and groundwater).

## 0.4.4
- Fixed: works with newer VTK versions that no longer have `AddActor2D` (legend and colour bar use `AddViewProp`).

## 0.4.3
- Added: a clear message with the fix when the Python package `vtk` (or `scipy`, `numpy`) is missing.
- Added: README note on installing VTK.

## 0.4.2
- Fixed: the cut-away also hides the **groundwater contour labels** on the cut side.

## 0.4.1
- Added: the cut-away also hides labels (borehole names, plume result labels) on the cut side; they can be ticked under *Layers included in the cut*.

## 0.4.0
- Added: **site boundary** (polygon or line layer) draped on the DEM, with colour, line width and lift above the surface; other coordinate systems are converted; saved in setups.

## 0.3.9
- Changed: the *Cut-away* section now sits between *Plumes* and *Tools*.

## 0.3.8
- Changed: the demo opens with vertical exaggeration 3 and a "Demo Model" title text box.

## 0.3.7
- Changed: in the Plumes tab, *Samples and their results* is its own group between *Data* and *Estimation between samples*.

## 0.3.6
- Changed: *Samples and their results* briefly sat inside the Data group (replaced by 0.3.7).

## 0.3.5
- Changed: borehole names have a separate horizontal and vertical offset (pixels, plus or minus).

## 0.3.4
- Added: borehole name styling: size, font, colour, bold, italic, background, overlap hiding. Applies live and is saved in setups.

## 0.3.3
- Fixed: gap between thin units (for example silty sand between sand and gravel) at the geology outline wall. Solids are now cut as volumes and stay watertight.

## 0.3.2
- Fixed: the chosen Light/Dark theme is remembered when the plugin is reopened.

## 0.3.1
- Changed: **kriging** is the default interpolation everywhere, including the geology model.

## 0.3.0
- Fixed: lists and text boxes in the panel (for example the chronology) are readable in light mode.

## 0.2.9
- Changed: the geology outline cut uses exact vertical walls along the convex hull of the boreholes; the margin moves the walls.

## 0.2.8
- Added: geology limited to the **convex hull of the boreholes** (option and margin in Model settings).

## 0.2.7
- Added: **Smooth geology solids** setting (layer order kept, cut faces still capped).

## 0.2.6
- Added: **DEM height adjustment** (up or down) and a *Keep geology below the DEM by* gap.
- Fixed: an error when building the model.

## 0.2.5
- Changed: the geology model (solids, contacts, block model, fence, section) is cut at the DEM and never rises above the ground surface.

## 0.2.4
- Changed: hole names tidied like the result labels, axis numbers in full and smaller, styled tabs, smoother edges (FXAA).

## 0.2.3
- Changed: collapsed boxes shown as plain title lines, smaller legend and label defaults, colour bar title on two lines.
- Added: option to hide overlapping result labels.

## 0.2.2
- Changed: modern look for the toolbar (rounded buttons, blue *Update model* button).

## 0.2.1
- Changed: modern look for the left panel (soft cards, rounded boxes, blue accent), light and dark; switch under *Tools > Look*.

## 0.2.0
- Changed: **closed plume shells** (not hollow where cut) with coloured cross-section cut faces; option to switch off.

## 0.1.9
- Fixed: the Cloud display no longer shows as a solid blue box; it follows the shell levels, lowest value, smoothing, fade and density.

## 0.1.8
- Added: new plume display **Cloud** (smooth volume rendering) next to the nested shells, soil and groundwater.

## 0.1.7
- Changed: plume shells are closed where they are cut (ground, water table, well bottoms, hull); tick box to switch off.

## 0.1.6
- Fixed: the plume could fail to draw when a shell contained invalid points.

## 0.1.5
- Added: plume shells **fade with distance from the nearest sample** (confidence cue), soil and groundwater.

## 0.1.4
- Fixed: the lithology log on the borehole tubes shows sharp boundaries between units (colours were blended).

## 0.1.3
- Added: *Save* and *Save as* buttons, the title bar shows the setup file in use, a *New* button to start a fresh scene.
- Added: AI-assistance note in the README.

## 0.1.2
- Fixed: contact e-mail corrected.

## 0.1.1
- Changed: security scan fixes; no silent error handling (errors are logged). The 2D View and Tables tabs start closed.

## 0.1.0 - First release
3D boreholes and lithology, DEM with satellite drape, groundwater surface, geological model, fence diagram, cut-away, soil and groundwater plumes, plume volume above a guideline, text boxes, HTML export, and data preparation from Excel / CSV.
