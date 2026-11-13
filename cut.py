"""Cutting planes for the 3D scene: one plane each for Easting (X), Northing (Y) and elevation."""
from vtkmodules.vtkCommonDataModel import vtkPlane, vtkPlaneCollection
from vtkmodules.vtkFiltersCore import vtkClipPolyData, vtkPolyDataNormals, vtkTriangleFilter, vtkCleanPolyData
from vtkmodules.vtkFiltersGeneral import vtkClipClosedSurface
from vtkmodules.vtkFiltersSources import vtkPlaneSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

FAR = 1.0e9


def prepare_closed(mesh):
    """Triangulates a solid and merges its duplicated points, so it is a closed surface that can be capped."""
    tri = vtkTriangleFilter()
    tri.SetInputData(mesh)
    tri.Update()
    cl = vtkCleanPolyData()
    cl.SetInputData(tri.GetOutput())
    cl.PointMergingOn()
    cl.ToleranceIsAbsoluteOn()
    cl.SetAbsoluteTolerance(1e-4)
    cl.Update()
    return cl.GetOutput()


class CutController:
    """Clips the 3D elements with up to three planes. Solid units get their cut faces filled in.
    Every element has its own set of planes, so single elements can be left out of the cut."""

    def __init__(self, ren):
        self.ren = ren
        self.items = {}      # element name -> {"planes": [3 planes], "ccs": clip filter for closed solids or None}
        self._keep = []      # keeps every filter and input alive for as long as the controller lives
        self.markers = []
        self.bounds0 = None
        self.spec = None
        self.included = None

    @staticmethod
    def _new_planes():
        planes = []
        for i in range(3):
            pl = vtkPlane()
            n = [0.0, 0.0, 0.0]
            n[i] = 1.0
            o = [0.0, 0.0, 0.0]
            o[i] = -FAR
            pl.SetNormal(*n)
            pl.SetOrigin(*o)
            planes.append(pl)
        return planes

    def attach(self, registry):
        """Puts the clipping filters in front of every 3D element listed in the registry."""
        self.bounds0 = self.ren.ComputeVisiblePropBounds()
        for e in registry:
            if e["name"] == "Block model" or not e["actors"]:
                continue
            a = e["actors"][0]
            if not isinstance(a, vtkActor):
                continue
            m = a.GetMapper()
            if m is None or not hasattr(m, "SetInputConnection"):
                continue
            planes = self._new_planes()
            if e.get("closed") and e.get("raw") is not None:
                ccs = vtkClipClosedSurface()
                ccs.SetInputData(e["raw"])
                ccs.GenerateFacesOn()
                ccs.GenerateOutlineOff()
                coll = vtkPlaneCollection()
                for pl in planes:
                    coll.AddItem(pl)
                ccs.SetClippingPlanes(coll)
                nf = vtkPolyDataNormals()
                nf.SetInputConnection(ccs.GetOutputPort())
                nf.SetFeatureAngle(50.0)
                nf.SplittingOn()
                nf.ConsistencyOn()
                self._keep += [e["raw"], coll, ccs, nf]
                m.SetInputConnection(nf.GetOutputPort())
                self.items[e["name"]] = {"planes": planes, "ccs": ccs}
            else:
                m.Update()
                poly = m.GetInput()            # the element's mesh itself, not the mapper's connection to it
                if poly is None:
                    continue
                self._keep.append(poly)
                prev = None
                for pl in planes:
                    c = vtkClipPolyData()
                    if prev is None:
                        c.SetInputData(poly)
                    else:
                        c.SetInputConnection(prev)
                    c.SetClipFunction(pl)
                    prev = c.GetOutputPort()
                    self._keep.append(c)
                m.SetInputConnection(prev)
                self.items[e["name"]] = {"planes": planes, "ccs": None}

    def set_planes(self, spec, included=None):
        """spec: three (enabled, value, keep) tuples for X, Y, Z in scene coordinates; keep = +1 keeps the side
        with larger values, -1 the side with smaller values. included: names of the elements to cut
        (None = all of them)."""
        self.spec, self.included = spec, included
        for name, it in self.items.items():
            cut_this = included is None or name in included
            for i, (on, val, keep) in enumerate(spec):
                pl = it["planes"][i]
                n = [0.0, 0.0, 0.0]
                n[i] = 1.0 if keep > 0 else -1.0
                o = [0.0, 0.0, 0.0]
                o[i] = val if (on and cut_this) else (-FAR if keep > 0 else FAR)
                pl.SetNormal(*n)
                pl.SetOrigin(*o)
            if it["ccs"] is not None:
                it["ccs"].Modified()
        self.update_markers(spec)

    def update_markers(self, spec):
        for a in self.markers:
            self.ren.RemoveActor(a)
        self.markers = []
        b = self.bounds0
        if not b or b[0] > b[1]:
            return
        for i, (on, val, keep) in enumerate(spec):
            if not on:
                continue
            src = vtkPlaneSource()
            if i == 0:
                src.SetOrigin(val, b[2], b[4]); src.SetPoint1(val, b[3], b[4]); src.SetPoint2(val, b[2], b[5])
            elif i == 1:
                src.SetOrigin(b[0], val, b[4]); src.SetPoint1(b[1], val, b[4]); src.SetPoint2(b[0], val, b[5])
            else:
                src.SetOrigin(b[0], b[2], val); src.SetPoint1(b[1], b[2], val); src.SetPoint2(b[0], b[3], val)
            mp = vtkPolyDataMapper()
            mp.SetInputConnection(src.GetOutputPort())
            act = vtkActor()
            act.SetMapper(mp)
            prop = act.GetProperty()
            prop.SetColor(0.25, 0.5, 0.95)
            prop.SetOpacity(0.12)
            prop.EdgeVisibilityOn()
            prop.SetEdgeColor(0.1, 0.3, 0.8)
            prop.SetLineWidth(1.5)
            prop.LightingOff()
            self.ren.AddActor(act)
            self.markers.append(act)
