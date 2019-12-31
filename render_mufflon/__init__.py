import bpy
import bgl
import os
from . import engine
from .engine import MufflonEngine
from .bindings import Device

bl_info = {
    "name": "Mufflon Render Engine",
    "description": "Mufflon Render Engine",
    "author": "Florian Bethe",
    "version": (1, 1),
    "blender": (2, 81, 0),
    "category": "Render"
}

class MufflonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    
    filepath_mufflon: bpy.props.StringProperty(
                name="Binary Location",
                description="Path to renderer DLLs",
                subtype='FILE_PATH',
                )
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "filepath_mufflon")

class MufflonRenderEngine(bpy.types.RenderEngine):
    # These three members are used by blender to set up the
    # RenderEngine; define its internal name, visible name and capabilities.
    bl_idname = "Mufflon"
    bl_label = "Mufflon"
    bl_use_preview = False
    bl_use_eevee_viewport = True
    bl_use_shading_nodes_custom = False
    
    @staticmethod
    def locate_binary():
        addon_prefs = bpy.context.preferences.addons[__package__].preferences

        # Use the system preference if its set.
        mufflon_binary = addon_prefs.filepath_mufflon
        if mufflon_binary:
            if os.path.exists(mufflon_binary):
                return mufflon_binary
            else:
                print("User Preferences path to Mofflon %r NOT FOUND, checking $PATH" % mufflon_binary)

        # This is taken from the POVray addon
        # search the path all os's
        mufflon_binary_default = "core.dll"

        os_path_ls = os.getenv("PATH").split(':') + [""]

        for dir_name in os_path_ls:
            mufflon_binary_default = os.path.join(dir_name, mufflon_binary_default)
            if os.path.exists(mufflon_binary_default):
                return mufflon_binary_default
        return None

    # Init is called whenever a new render engine instance is created. Multiple
    # instances may exist at the same time, for example for a viewport and final
    # render.
    def __init__(self):
        self.scene_data = None
        self.draw_data = None
        self.first_time = True
        
        # Locate the DLLs
        addon_prefs = bpy.context.preferences.addons[__package__].preferences
        mufflon_binary = self.locate_binary()
        if not mufflon_binary:
            self.report({'ERROR'}, ("No binary path for Mufflon has been specified"))
            raise Exception("No binary path for Mufflon has been specified")
        self.engine = MufflonEngine(mufflon_binary)
        self.renderable = False

    # When the render engine instance is destroy, this is called. Clean up any
    # render engine data here, for example stopping running render threads.
    def __del__(self):
        pass

    # This is the method called by Blender for both final renders (F12) and
    # small preview for materials, world and lights.
    def render(self, depsgraph):
        if not self.renderable:
            return
        scene = depsgraph.scene
        scale = scene.render.resolution_percentage / 100.0
        self.size_x = int(scene.render.resolution_x * scale)
        self.size_y = int(scene.render.resolution_y * scale)
        
        try:
            self.engine.prepare_render(self.size_x, self.size_y, scene.mufflon.min_path_length, scene.mufflon.max_path_length,
                                       scene.mufflon.nee_count, scene.mufflon.merge_radius, scene.mufflon.integrator,
                                       Device.CUDA if (scene.mufflon.device == 'CUDA') and (scene.mufflon.integrator in engine.CUDA_INTEGRATORS) else Device.CPU)
            # Here we write the pixel values to the RenderResult
            result = self.begin_result(0, 0, self.size_x, self.size_y)
            layer = result.layers[0].passes["Combined"]
            pixels = [[0.0, 0.0, 0.0, 0.0]] * self.size_x * self.size_y
            for s in range(scene.mufflon.samples):
                layer.rect = self.engine.render_iteration(self.size_x, self.size_y, pixels)
                self.update_result(result)
            self.end_result(result)
        except Exception as e:
            self.report({'ERROR'}, ("%s (DLL message: '%s')"%(str(e), self.engine.get_last_error())))
            

    # For viewport renders, this method gets called once at the start and
    # whenever the scene or 3D viewport changes. This method is where data
    # should be read from Blender in the same thread. Typically a render
    # thread will be started to do the work while keeping Blender responsive.
    def view_update(self, context, depsgraph):
        self.renderable = True
        try:
            self.engine.update(context.blend_data, depsgraph)
        except Exception as e:
            self.renderable = False
            self.report({'ERROR'}, ("%s (DLL message: '%s')"%(str(e), self.engine.get_last_error())))
        
    def update(self, data, depsgraph):
        self.renderable = True
        try:
            self.engine.update(data, depsgraph)
        except Exception as e:
            self.renderable = False
            self.report({'ERROR'}, ("%s (DLL message: '%s')"%(str(e), self.engine.get_last_error())))

    # For viewport renders, this method is called whenever Blender redraws
    # the 3D viewport. The renderer is expected to quickly draw the render
    # with OpenGL, and not perform other expensive work.
    # Blender will draw overlays for selection and editing on top of the
    # rendered image automatically.
    def view_draw(self, context, depsgraph):
        if not self.renderable:
            return
        region = context.region
        scene = depsgraph.scene

        # Get viewport dimensions
        dimensions = region.width, region.height

        # Bind shader that converts from scene linear to display space,
        bgl.glEnable(bgl.GL_BLEND)
        bgl.glBlendFunc(bgl.GL_ONE, bgl.GL_ONE_MINUS_SRC_ALPHA)
        self.bind_display_space_shader(scene)

        if not self.draw_data or self.draw_data.dimensions != dimensions:
            self.draw_data = CustomDrawData(dimensions)
        try:
            self.engine.update_viewport_camera(context.space_data, scene.render.resolution_y / scene.render.resolution_x)
            self.engine.prepare_render(region.width, region.height,  scene.mufflon.min_path_length, scene.mufflon.max_path_length,
                                       scene.mufflon.nee_count, scene.mufflon.merge_radius, scene.mufflon.integrator,
                                       Device.CUDA if (scene.mufflon.device == 'CUDA') and (scene.mufflon.integrator in engine.CUDA_INTEGRATORS) else Device.CPU)
            # TODO: how to render it piece by piece
            for s in range(scene.mufflon.preview_samples):
                pixels = self.engine.render_iteration(region.width, region.height, None)
                self.draw_data.draw(pixels)
        except Exception as e:
            self.report({'ERROR'}, ("%s (DLL message: '%s')"%(str(e), self.engine.get_last_error())))

        self.unbind_display_space_shader()
        bgl.glDisable(bgl.GL_BLEND)


class CustomDrawData:
    def __init__(self, dimensions):
        # Generate dummy float image buffer
        self.dimensions = dimensions
        width, height = dimensions

        pixels = [0.1, 0.2, 0.1, 1.0] * width * height
        pixels = bgl.Buffer(bgl.GL_FLOAT, width * height * 4, pixels)

        # Generate texture
        self.texture = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glGenTextures(1, self.texture)
        bgl.glActiveTexture(bgl.GL_TEXTURE0)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, self.texture[0])
        bgl.glTexImage2D(bgl.GL_TEXTURE_2D, 0, bgl.GL_RGBA16F, width, height, 0, bgl.GL_RGBA, bgl.GL_FLOAT, pixels)
        bgl.glTexParameteri(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_MIN_FILTER, bgl.GL_LINEAR)
        bgl.glTexParameteri(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_MAG_FILTER, bgl.GL_LINEAR)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, 0)

        # Bind shader that converts from scene linear to display space,
        # use the scene's color management settings.
        shader_program = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glGetIntegerv(bgl.GL_CURRENT_PROGRAM, shader_program)

        # Generate vertex array
        self.vertex_array = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glGenVertexArrays(1, self.vertex_array)
        bgl.glBindVertexArray(self.vertex_array[0])

        texturecoord_location = bgl.glGetAttribLocation(shader_program[0], "texCoord")
        position_location = bgl.glGetAttribLocation(shader_program[0], "pos")

        bgl.glEnableVertexAttribArray(texturecoord_location)
        bgl.glEnableVertexAttribArray(position_location)

        # Generate geometry buffers for drawing textured quad
        position = [0.0, 0.0, width, 0.0, width, height, 0.0, height]
        position = bgl.Buffer(bgl.GL_FLOAT, len(position), position)
        texcoord = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
        texcoord = bgl.Buffer(bgl.GL_FLOAT, len(texcoord), texcoord)

        self.vertex_buffer = bgl.Buffer(bgl.GL_INT, 2)

        bgl.glGenBuffers(2, self.vertex_buffer)
        bgl.glBindBuffer(bgl.GL_ARRAY_BUFFER, self.vertex_buffer[0])
        bgl.glBufferData(bgl.GL_ARRAY_BUFFER, 32, position, bgl.GL_STATIC_DRAW)
        bgl.glVertexAttribPointer(position_location, 2, bgl.GL_FLOAT, bgl.GL_FALSE, 0, None)

        bgl.glBindBuffer(bgl.GL_ARRAY_BUFFER, self.vertex_buffer[1])
        bgl.glBufferData(bgl.GL_ARRAY_BUFFER, 32, texcoord, bgl.GL_STATIC_DRAW)
        bgl.glVertexAttribPointer(texturecoord_location, 2, bgl.GL_FLOAT, bgl.GL_FALSE, 0, None)

        bgl.glBindBuffer(bgl.GL_ARRAY_BUFFER, 0)
        bgl.glBindVertexArray(0)

    def __del__(self):
        bgl.glDeleteBuffers(2, self.vertex_buffer)
        bgl.glDeleteVertexArrays(1, self.vertex_array)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, 0)
        bgl.glDeleteTextures(1, self.texture)

    def draw(self, pixels):
        width, height = self.dimensions
        bgl.glActiveTexture(bgl.GL_TEXTURE0)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, self.texture[0])
        pixels = bgl.Buffer(bgl.GL_FLOAT, width * height * 4, pixels)
        bgl.glTexSubImage2D(bgl.GL_TEXTURE_2D, 0, 0, 0, width, height, bgl.GL_RGBA, bgl.GL_FLOAT, pixels)
        bgl.glBindVertexArray(self.vertex_array[0])
        bgl.glDrawArrays(bgl.GL_TRIANGLE_FAN, 0, 4)
        bgl.glBindVertexArray(0)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, 0)


# RenderEngines also need to tell UI Panels that they are compatible with.
# We recommend to enable all panels marked as BLENDER_RENDER, and then
# exclude any panels that are replaced by custom panels registered by the
# render engine, or that are not supported.
def get_panels():
    # TODO: Only include the panels we can use
    exclude_panels = {
        'VIEWLAYER_PT_filter',
        'VIEWLAYER_PT_layer_passes',
    }

    panels = []
    for panel in bpy.types.Panel.__subclasses__():
        if hasattr(panel, 'COMPAT_ENGINES') and 'BLENDER_RENDER' in panel.COMPAT_ENGINES:
            if panel.__name__ not in exclude_panels:
                panels.append(panel)

    return panels


def register():
    from . import ui
    
    # Register the RenderEngine
    bpy.utils.register_class(MufflonRenderEngine)
    bpy.utils.register_class(MufflonPreferences)
    ui.register()
    
    #for panel in get_panels():
        #compatList = panel.COMPAT_ENGINES.copy()
        #compatList.add('Mufflon')
        #panel.COMPAT_ENGINES = compatList


def unregister():
    from . import ui
    
    bpy.utils.unregister_class(MufflonRenderEngine)
    ui.unregister()
