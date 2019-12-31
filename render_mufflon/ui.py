import bpy
from enum import Enum

# TODO: Discover renderers at runtime?
Integrators = [
    ("PT", "Pathtracer", "", 0),
    ("LT", "Lighttracer", "", 1),
    ("WF", "Wireframe", "", 2),
    ("BPT", "Bidirectional Pathtracer", "", 3),
    ("BPM", "Bidirectional Pathtracer", "", 4),
    ("NEB", "Next Event Backtracking", "", 5),
    ("VCM", "Vertex Connection and Merging", "", 6),
    ("IVCM", "Improved Vertex Connection and Merging", "", 7)
]
RenderDevices = [
    ("CPU", "CPU", "", 0),
    ("CUDA", "CUDA", "", 1)
]

class MufflonRenderProperties(bpy.types.PropertyGroup):
    integrator: bpy.props.EnumProperty(
        items = Integrators,
        description = "Integrator used for rendering",
        default = "PT"
    )
    device: bpy.props.EnumProperty(
        items = RenderDevices,
        description = "Device used for rendering",
        default = "CPU"
    )
    min_path_length: bpy.props.IntProperty(
        name = "Min. path length",
        description = "Min. length of sample path",
        min = 0,
        default = 0
    )
    max_path_length: bpy.props.IntProperty(
        name = "Max. path length",
        description = "Max. length of sample path",
        min = 1,
        default = 8
    )
    nee_count: bpy.props.IntProperty(
        name = "NEE count",
        description = "Number of light connections for next-event estimation",
        min = 1,
        default = 1
    )
    merge_radius: bpy.props.FloatProperty(
        name = "Merge radius",
        description = "Radius in which a photon contributes to a sample",
        min = 0.0000001,
        default = 0.0001
    )
    samples: bpy.props.IntProperty(
        name = "Samples",
        description = "Render samples for final render",
        min = 0,
        default = 16
    )
    preview_samples: bpy.props.IntProperty(
        name = "Viewport samples",
        description = "Render samples for viewport render",
        min = 0,
        default = 4
    )
    

class MUFFLON_RENDER_PT_sampling(bpy.types.Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"
    COMPAT_ENGINES = {'Mufflon'}
    bl_label = "Sampling"

    @classmethod
    def poll(cls, context):
        return context.engine in cls.COMPAT_ENGINES

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="My Select Panel")

    def draw(self, context):
        from .engine import (CUDA_INTEGRATORS, NEE_INTEGRATORS, MERGE_INTEGRATORS)
        layout = self.layout
        
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        scene = context.scene
        mscene = scene.mufflon
        col = layout.column(align=True)
        layout.prop(mscene, "integrator", text="Integrator")
        if mscene.integrator in CUDA_INTEGRATORS:
            layout.prop(mscene, "device", text="Device")
        layout.prop(mscene, "min_path_length", text="Min. path length")
        layout.prop(mscene, "max_path_length", text="Max. path length")
        layout.prop(mscene, "samples", text="Render")
        layout.prop(mscene, "preview_samples", text="Viewport")
        if mscene.integrator in NEE_INTEGRATORS:
            layout.prop(mscene, "nee_count", text="Light connections")
        if mscene.integrator in MERGE_INTEGRATORS:
            layout.prop(mscene, "merge_radius", text="Merge radius")
        
classes = (
    MUFFLON_RENDER_PT_sampling,
    MufflonRenderProperties
)

def register():
    from bpy.utils import register_class
    
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.mufflon = bpy.props.PointerProperty(type=MufflonRenderProperties)

def unregister():
    from bpy.utils import unregister_class
    
    for cls in classes:
        unregister_class(cls)