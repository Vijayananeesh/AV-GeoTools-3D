def classFactory(iface):
    from .plugin import Subsurface3DPlugin
    return Subsurface3DPlugin(iface)
