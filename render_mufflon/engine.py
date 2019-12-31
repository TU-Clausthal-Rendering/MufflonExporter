import bpy
import bmesh
import mathutils
import math
from enum import Enum
from mathutils import Vector
from . import (bindings, lights, materials, util)
from ctypes import *
from .bindings import *
from .util import *
from .lights import add_lights
from .materials import add_materials

NEE_INTEGRATORS = ['PT', 'LT']
MERGE_INTEGRATORS = ['NEB', 'VCM', 'IVCM']
CUDA_INTEGRATORS = ['PT', 'LT', 'WF']

INVALID_MATERIAL_IDX = 65535

# Check if an object is a renderable instance
def is_instance(obj):
    # Not used by any scene
    if obj.users == 0:
        return False
    # At the moment only meshes and perfect spheres can be exported.
    # Maybe there is an NURBS or Bezier-spline export in feature...
    # 'mufflon_sphere' is a custom property used to flag perfect spheres.
    # Camera, Lamp, ... are skipped by this if, too.
    if obj.type != "MESH":# and not obj.mufflon_sphere:
        return False
    # Skip meshes which do not have any faces
    if obj.type == "MESH" and len(obj.data.polygons) == 0:
        return False
    return True
    
def get_instance_transformation(instance):
    #if instance.mufflon_sphere:
    #    return flip_space_mat(mathutils.Matrix.Translation(instance.location) @ mathutils.Matrix.Scale(instance.scale[0], 4))
    #else:
    return flip_space_mat(instance.matrix_world)

def get_faces_to_triangulate(bm):
    return [f for f in bm.faces if len(f.edges) > 4]

def get_edges_to_split(bm):
    edgesToSplit = set([e for e in bm.edges if e.seam or not e.smooth])
    
    for f in bm.faces:
        if not f.smooth:
            for e in f.edges:
                edgesToSplit.add(e)
    return list(edgesToSplit)
    
def count_tri_quads(bm):
    numberOfTriangles = 0
    numberOfQuads = 0
    for face in bm.faces:
        numVertices = len(face.verts)
        if numVertices == 3:
            numberOfTriangles += 1
        elif numVertices == 4:
            numberOfQuads += 1
        else:
            return 0, 0
    return numberOfTriangles, numberOfQuads

def prepare_object_mesh(depsgraph, lod):
    # Disabling the armature modifier so we get rest-pose vertex positions is done in export_binary
    mesh = lod.evaluated_get(depsgraph).data    # applies all modifiers
    bm = bmesh.new()
    bm.from_mesh(mesh)  # bmesh gives a local editable mesh copy
    facesToTriangulate = get_faces_to_triangulate(bm)
    if len(facesToTriangulate) > 0:
        bmesh.ops.triangulate(bm, faces=facesToTriangulate, quad_method='BEAUTY', ngon_method='BEAUTY')
    # Split vertices if vertex has multiple uv coordinates (is used in multiple triangles)
    # or if it is not smooth
    
    if len(lod.data.uv_layers) > 0:
        # mark seams from uv islands
        if bpy.ops.uv.seams_from_islands.poll():
            bpy.ops.uv.seams_from_islands()
    edgesToSplit = get_edges_to_split(bm)
    if len(edgesToSplit) > 0:
        bmesh.ops.split_edges(bm, edges=edgesToSplit)

    tris, quads = count_tri_quads(bm)
    # Only change the mesh if any changes have been performed
    if len(facesToTriangulate) > 0 or len(edgesToSplit) > 0:
        bm.to_mesh(mesh)
    bm.free()
    return mesh, tris, quads
    
def get_pinhole_fov(camera, width, height):
    # FOV might be horizontal or vertically, see https://blender.stackexchange.com/a/38571
    if height > width:
        return camera.data.angle
    else:
        # correct aspect ratio because camera.angle is defined in x direction
        return math.atan(math.tan(camera.data.angle / 2) * height / width) * 2
        
def add_camera(interface, camera, width, height):
    if camera.data.type != "PERSP":
        raise Exception("Invalid camera type")
    trans, rot, scale = camera.matrix_world.decompose()
    camPos = to_vec3(flip_space(camera.location))
    camDir = to_vec3(flip_space(rot @ Vector((0.0, 0.0, -1.0))))
    camUp = to_vec3(flip_space(rot @ Vector((0.0, 1.0, 0.0))))
    
    aperture = camera.data.dof.aperture_fstop if camera.data.dof.use_dof else 128.0
    if aperture >= 128.0:
        return interface.world_add_pinhole_camera(camera.name, camPos, camDir, camUp, 1, 0.1, 5000.0, get_pinhole_fov(camera, width, height))
    else:
        # TODO
        aspectRatio = height / width
        chipHeight = math.tan(camera.data.angle / 2.0) * 2.0 * camera.data.lens * aspectRatio
        lensRad = camera.data.lens / (2.0 * aperture)
        return interface.world_add_focus_camera(camera.name, camPos, camDir, camUp, 1, 0.1, 5000.0, camera.data.lens,
                                                camera.data.dof.focus_distance, 0.0, chipHeight)
    
class MufflonEngine:
    def __init__(self, mufflon_binary):
        self.renderer = bindings.RenderActions(binary_path=mufflon_binary, useLoader=False)
        self.scenarioHdl = c_void_p(0)
        self.cameraHdl = c_void_p(0)
        self.lightCount = 0
        self.renderer.set_renderer_log_level(LogLevel.PEDANTIC)
    
    def __del__(self):
        del self.renderer
        
    def get_last_error(self):
        return self.renderer.dllInterface.core_get_dll_error()
    
    def update(self, data, depsgraph):
        self.renderer.dllInterface.world_clear_all()
        camera = depsgraph.scene.camera
        scene = depsgraph.scene
        scale = scene.render.resolution_percentage / 100.0
        width = int(scene.render.resolution_x * scale)
        height = int(scene.render.resolution_y * scale)
        instanceCount = 0
        meshes = {}
        lights = []
        for obj in data.objects:
            if is_instance(obj):
                if obj.data in meshes:
                    meshes[obj.data][1].append(obj)
                else:
                    meshes[obj.data] = (c_void_p(), [obj])
                instanceCount += 1
            elif obj.type == 'LIGHT':
                lights.append(obj)
        
        # Add materials
        materialHdls = add_materials(self.renderer.dllInterface, data.materials)
        # Construct index-lookup for materials
        materialIndices = {}
        for i in range(len(data.materials)):
            materialIndices[data.materials[i]] = i
        
        # Add meshes(objects) and instances
        instanceHdls = []
        if not self.renderer.dllInterface.world_reserve_objects_instances(len(meshes), instanceCount):
            raise Exception("Failed to reserve objects/instances (%d/%d)"%(len(meshes), instanceCount))
        for mesh, meshTuple in meshes.items():
            if len(mesh.materials) == 0:
                raise Exception("Mesh '%s' has no materials"%(mesh.name))
            objHdl = self.renderer.dllInterface.world_create_object(mesh.name, 0)
            lodHdl = self.renderer.dllInterface.dllHolder.core.object_add_lod(objHdl, 0);
            evalMesh, tris, quads = prepare_object_mesh(depsgraph, meshTuple[1][0])
            if not self.renderer.dllInterface.dllHolder.core.polygon_reserve(lodHdl, len(evalMesh.vertices),
                                                                             len(evalMesh.edges), tris, quads):
                raise Exception("Failed to reserve polygon '%s' data (%d/%d/%d/%d)"%(mesh.name, len(evalMesh.vertices), len(evalMesh.edges), tris, quads))
            for v in evalMesh.vertices:
                point = Vec3(v.co[0], v.co[1], v.co[2])
                normal = Vec3(v.normal[0], v.normal[1], v.normal[2])
                uv = Vec2(0, 0)
                if self.renderer.dllInterface.dllHolder.core.polygon_add_vertex(lodHdl, point, normal, uv) == -1:
                    raise Exception("Failed to add vertex to polygon '%s'"%(mesh.name))
            
            # Get the list of material indices
            localMatIndices = [0] * len(mesh.materials)
            for i in range(len(mesh.materials)):
                localMatIndices[i] = materialIndices[mesh.materials[i]]
            
            for polygon in evalMesh.polygons:
                if len(polygon.vertices) == 3:
                    indices = UVec3(polygon.vertices[0], polygon.vertices[1], polygon.vertices[2])
                    if self.renderer.dllInterface.dllHolder.core.polygon_add_triangle_material(lodHdl, indices, 0) == -1:
                        raise Exception("Failed to add triangle to polygon '%s'(probably a non-manifold mesh)"%(mesh.name))
                    # TODO: material index
            for polygon in evalMesh.polygons:
                if len(polygon.vertices) == 4:
                    indices = UVec4(polygon.vertices[0], polygon.vertices[1], polygon.vertices[2], polygon.vertices[3])
                    if polygon.material_index >= len(localMatIndices):
                        raise Exception("Mesh '%s' has polygon without assigned material"%(mesh.name))
                    if self.renderer.dllInterface.dllHolder.core.polygon_add_quad_material(lodHdl, indices, localMatIndices[polygon.material_index]) == -1:
                        raise Exception("Failed to add quad to polygon '%s'(probably a non-manifold mesh)"%(mesh.name))
            
            # Create all instances for this mesh(object)
            for i in meshTuple[1]:
                instHdl = self.renderer.dllInterface.world_create_instance(objHdl, 0xFFFFFFFF)
                if instHdl == c_void_p(0):
                    raise Exception("Failed to create instance '%s'"%(i.name))
                transMat = get_instance_transformation(i)
                if not self.renderer.dllInterface.instance_set_transformation_matrix(instHdl, (c_float * 12)(*mat4x4_to_cfloat_array(transMat)), 0):
                        raise Exception("Failed to set transformation matrix for instance '%s'"%(i.name))
                instanceHdls.append(instHdl)
         
        # Load the camera
        self.cameraHdl = add_camera(self.renderer.dllInterface, camera, width, height)
        if self.cameraHdl == c_void_p(0):
            raise Exception("Failed to create camera '%s'"%(camera.name))
                                                                               
        # Load lights
        lightHdls = add_lights(self.renderer.dllInterface, lights)
        self.lightCount = len(lightHdls)
        errMsg = c_char_p(0)
        if not self.renderer.dllInterface.world_finalize(errMsg):
            raise Exception(errMsg.value)
        
        # Scenario
        if not self.renderer.dllInterface.world_reserve_scenarios(1):
            raise Exception("Failed to reserve scenario")
        self.scenarioHdl = self.renderer.dllInterface.world_create_scenario("Scene")
        if self.scenarioHdl == c_void_p(0):
            raise Exception("Failed to create render scenario")
        if not self.renderer.dllInterface.scenario_set_camera(self.scenarioHdl, self.cameraHdl):
            raise Exception("Failed to set camera for render scenario")
        if not self.renderer.dllInterface.dllHolder.core.scenario_set_resolution(self.scenarioHdl, width, height):
            raise Exception("Failed to set resolution for render scenario")
        for l in lightHdls:
            if not self.renderer.dllInterface.scenario_add_light(self.scenarioHdl, l):
                raise Exception("Failed to add light to render scenario")
        if not self.renderer.dllInterface.dllHolder.core.scenario_reserve_material_slots(self.scenarioHdl, len(materialHdls)):
            raise Exception("Failed to reserve material slots for render scenario")
        for i in range(len(data.materials)):
            material = data.materials[i]
            matSlot = self.renderer.dllInterface.dllHolder.core.scenario_declare_material_slot(self.scenarioHdl, material.name.encode('utf-8'), len(material.name))
            if matSlot == INVALID_MATERIAL_IDX:
                raise Exception("Failed to declare material slot in render scenario")
            if not self.renderer.dllInterface.dllHolder.core.scenario_assign_material(self.scenarioHdl, matSlot, materialHdls[i]):
                raise Exception("Failed to associate material with render scenario")
        if not self.renderer.dllInterface.world_finalize_scenario(self.scenarioHdl, errMsg):
            raise Exception(errMsg.value)
        
    def update_viewport_camera(self, spaceView3D, aspectRatio):
        r3d = spaceView3D.region_3d
        # TODO: orthographic viewport
        pos = to_vec3(flip_space(r3d.view_matrix.inverted().translation))
        dir = to_vec3(flip_space(r3d.view_rotation @ mathutils.Vector((0.0, 0.0, -1.0))))
        up = to_vec3(flip_space(r3d.view_rotation @ mathutils.Vector((0.0, 1.0, 0.0))))
        # Compute FoV from the current camera's sensor height (given in blender in [mm])
        # Since Blender's FoV is the exact opposite of ours, convert
        fov = 2.0 * math.atan(spaceView3D.camera.data.sensor_width * aspectRatio / (2.0 * spaceView3D.lens))
        if not self.renderer.dllInterface.dllHolder.core.world_set_camera_position(self.cameraHdl, pos, 0):
            raise Exception("Failed to set camera position")
        if not self.renderer.dllInterface.dllHolder.core.world_set_camera_direction(self.cameraHdl, dir, up, 0):
            raise Exception("Failed to set camera direction")
        if not self.renderer.dllInterface.dllHolder.core.world_set_pinhole_camera_fov(self.cameraHdl, fov):
            raise Exception("Failed to set camera FoV")

    def prepare_render(self, width, height, minPathLength, maxPathLength, neeCount, mergeRadius, renderer, device):
        if not self.renderer.dllInterface.dllHolder.core.scenario_set_resolution(self.scenarioHdl, width, height):
            raise Exception("Failed to set render resolution")
        if self.renderer.dllInterface.world_load_scenario(self.scenarioHdl) == c_void_p(0):
            raise Exception("Failed to load scene")
        self.renderer.enable_renderer(renderer, device)
        if not self.renderer.renderer_set_parameter_int("Min. path length", minPathLength):
            raise Exception("Failed to set min. path length %d"%(minPathLength))
        if not self.renderer.renderer_set_parameter_int("Max. path length", maxPathLength):
            raise Exception("Failed to set max. path length %d"%(maxPathLength))
        if renderer in NEE_INTEGRATORS:
            if not self.renderer.renderer_set_parameter_int("NEE count", neeCount):
                raise Exception("Failed to set NEE count %d"%(neeCount))
        if renderer in MERGE_INTEGRATORS:
            if not self.renderer.renderer_set_parameter_float("Relative merge radius", mergeRadius):
                raise Exception("Failed to set merge radius %f"%(mergeRadius))
        if renderer == 'WF':
            renderTarget = 'Border'
        else:
            renderTarget = 'Radiance'
        self.renderer.enable_render_target(renderTarget, False)
        self.rectArray = (c_float * (4 * width * height))()
            
    def render_iteration(self, width, height, nestedPixels):
        if not self.renderer.dllInterface.render_iterate():
            raise Exception("Failed to render iteration")
        if not self.renderer.dllInterface.mufflon_get_target_image("Radiance", 0, POINTER(POINTER(c_float))()):
            raise Exception("Failed to get rendered image")
        if not self.renderer.dllInterface.mufflon_copy_screen_texture_rgba32(self.rectArray, 1.0):
            raise Exception("Failed to copy rendered image")
        if nestedPixels is not None:
            for i in range(width * height):
                nestedPixels[i] = [ self.rectArray[4 * i + 0], self.rectArray[4 * i + 1], self.rectArray[4 * i + 2], 1.0 ]
            return nestedPixels
        else:
            return self.rectArray