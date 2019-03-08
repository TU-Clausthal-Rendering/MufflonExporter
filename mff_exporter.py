import bpy
import bmesh
from mathutils import Vector
import mathutils
import os
import json
import collections
import struct
import numpy
import math
import zlib
from collections import OrderedDict
import re
from inspect import currentframe, getframeinfo
from collections.abc import Mapping, Sequence

bl_info = {
    "name": "Mufflon Exporter",
    "description": "Exporter for the custom Mufflon file format",
    "author": "Marvin",
    "version": (1, 1),
    "blender": (2, 69, 0),
    "location": "File > Export > Mufflon (.json/.mff)",
    "category": "Import-Export"
}



class CustomJSONEncoder(json.JSONEncoder):
    # https://stackoverflow.com/questions/50700585/write-json-float-in-scientific-notation
    def iterencode(self, o, _one_shot=False, level=1):
        indent = ' ' * (level * self.indent)
        if isinstance(o, float):
            return format(o, '.3g') # keep 3 most significant digits
        elif isinstance(o, Mapping):
            return "{{{}\n{}}}".format(','.join('\n{}"{}" : {}'.format(indent, str(ok), self.iterencode(ov, _one_shot, level+1))
                                             for ok, ov in o.items()), ' ' * ((level-1) * self.indent))
        elif isinstance(o, Sequence) and not isinstance(o, str):
            # Do not line break short arrays
            sep = ', ' if len(o) <= 4 else (',\n    '+indent)
            return "[{}]".format(sep.join(map(self.iterencode, o)))
        return ',\n'.join(super().iterencode(o))



# Blender has: up = z, but target is: up = y
def flip_space(vec):
    return [vec[0], vec[2], -vec[1]]

materialKeys = ["type", "albedo", "roughness", "ndf", "absorption", "radiance", "scale", "refractionIndex", "factorA", "factorB", "layerA", "layerB", "layerReflection", "layerRefraction"]
rootFilePath = ""


def make_path_relative_to_root(blenderPath):
    absPath = bpy.path.abspath(blenderPath)
    finalPath = os.path.relpath(absPath, rootFilePath)
    finalPath = finalPath.replace("\\", "/")
    return finalPath


def write_lambert_material(workDictionary, textureMap, material, applyFactor):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "lambert"
    if 'diffuse' in textureMap:
        workDictionary['albedo'] = make_path_relative_to_root(material.texture_slots[textureMap['diffuse']].texture.image.filepath)
    else:
        scale = material.diffuse_intensity if applyFactor else 1.0
        workDictionary['albedo'] = [
            material.diffuse_color.r * scale,
            material.diffuse_color.g * scale,
            material.diffuse_color.b * scale]

def write_orennayar_material(workDictionary, textureMap, material, applyFactor):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "orennayar"
    if 'diffuse' in textureMap:
        workDictionary['albedo'] = make_path_relative_to_root(material.texture_slots[textureMap['diffuse']].texture.image.filepath)
    else:
        scale = material.diffuse_intensity if applyFactor else 1.0
        workDictionary['albedo'] = [
            material.diffuse_color.r * scale,
            material.diffuse_color.g * scale,
            material.diffuse_color.b * scale]
    workDictionary['roughness'] = material.roughness

def write_torrance_material(workDictionary, textureMap, material, applyFactor, self):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "torrance"
    if 'specular' in textureMap:
        workDictionary['albedo'] = make_path_relative_to_root(material.texture_slots[textureMap['specular']].texture.image.filepath)
    else:
        scale = material.specular_intensity if applyFactor else 1.0
        workDictionary['albedo'] = [material.specular_color.r * scale,
                                    material.specular_color.g * scale,
                                    material.specular_color.b * scale]
    if 'roughness' in textureMap:
        workDictionary['roughness'] = make_path_relative_to_root(material.texture_slots[textureMap['roughness']].texture.image.filepath)
    else:
        if material.specular_shader == "COOKTORR" or material.specular_shader == "PHONG" or material.specular_shader == "BLINN":
            workDictionary['roughness'] = math.pow(1 - material.specular_hardness / 511, 3)  # Max hardness = 511
        else:
            self.report({'WARNING'}, ("Unsupported specular material: \"%s\". Exporting as Torrance material with roughness 0.1." % (material.name)))
            workDictionary['roughness'] = 0.1  # We have no roughness parameter so we default to 0.1
    if "ndf" in material:
        workDictionary['ndf'] = material["ndf"]
    else:
        workDictionary['ndf'] = "GGX"  # Default normal distribution function

def write_walter_material(workDictionary, textureMap, material):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "walter"
    if 'roughness' in textureMap:
        workDictionary['roughness'] = make_path_relative_to_root(material.texture_slots[textureMap['roughness']].texture.image.filepath)
    else:
        workDictionary['roughness'] = (1 - material.raytrace_transparency.gloss_factor)
    if "ndf" in material:
        workDictionary['ndf'] = material["ndf"]
    else:
        workDictionary['ndf'] = "GGX"  # Default normal distribution function
    absorptionFactor = math.pow(1-material.alpha, 2.0)
    workDictionary['absorption'] = [material.diffuse_color.r*absorptionFactor, material.diffuse_color.g*absorptionFactor, material.diffuse_color.b*absorptionFactor]
    workDictionary['ior'] = material.raytrace_transparency.ior

def write_fresnel_material(workDictionary, textureMap, material):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "fresnel"
    workDictionary['refractionIndex'] = material.diffuse_fresnel
    workDictionary['layerRefraction'] = collections.OrderedDict()
    workDictionary['layerReflection'] = collections.OrderedDict()

def write_blend_material(workDictionary, textureMap, material, factorA, factorB):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "blend"
    workDictionary['factorA'] = factorA
    workDictionary['factorB'] = factorB
    workDictionary['layerA'] = collections.OrderedDict()
    workDictionary['layerB'] = collections.OrderedDict()

def write_emissive_material(workDictionary, textureMap, material):
    for key in materialKeys: # Delete all material keys which might exist due to a change of the material
        workDictionary.pop(key, None)
    workDictionary['type'] = "emissive"
    if 'emissive' in textureMap:
        workDictionary['radiance'] = make_path_relative_to_root(material.texture_slots[textureMap['emissive']].texture.image.filepath)
    else:
        workDictionary['radiance'] = [material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b]
    workDictionary['scale'] = [material.emit, material.emit, material.emit]



def write_vertex_normals(vertexDataArray, mesh, use_compression):
    if mesh.has_custom_normals:
        # Create a lookuptable vertex -> any loop
        vertToLoop = [0] * len(mesh.vertices)
        for i,l in enumerate(mesh.loops):
            vertToLoop[l.vertex_index] = l
        # Write out the normals
        for k in range(len(mesh.vertices)):
            normal = vertToLoop[k].normal
            if use_compression:
                vertexDataArray.extend(pack_normal32(normal).to_bytes(4, byteorder='little', signed=True))
            else:
                vertexDataArray.extend(struct.pack('<3f', *normal))
    else:
        for k in range(len(mesh.vertices)):
            if use_compression:
                vertexDataArray.extend(pack_normal32(mesh.vertices[k].normal).to_bytes(4, byteorder='little', signed=True))
            else:
                vertexDataArray.extend(struct.pack('<3f', *mesh.vertices[k].normal))


def export_json(context, self, filepath, binfilepath, use_selection, overwrite_default_scenario):
    version = "1.1"
    binary = os.path.relpath(binfilepath, os.path.commonpath([filepath, binfilepath]))
    global rootFilePath; rootFilePath = os.path.dirname(filepath)

    scn = context.scene
    dataDictionary = collections.OrderedDict()

    if os.path.isfile(filepath):
        file = open(filepath, 'r')
        jsonStr = file.read()
        file.close()
        try:
            oldData = json.loads(jsonStr, object_pairs_hook=OrderedDict)  # loads the old json and preserves ordering
            dataDictionary = oldData.copy()
        except json.decoder.JSONDecodeError as e:
            self.report({'ERROR'}, "Old JSON has wrong format: " + str(e))
            return -1
        dataDictionary['version'] = version
        dataDictionary['binary'] = binary
        if overwrite_default_scenario or not ('defaultScenario' in oldData):
            dataDictionary['defaultScenario'] = context.scene.name
        else:
            dataDictionary['defaultScenario'] = oldData['defaultScenario']
        dataDictionary['cameras'] = oldData['cameras']
        dataDictionary['lights'] = oldData['lights']
        dataDictionary['materials'] = oldData['materials']
        dataDictionary['scenarios'] = oldData['scenarios']
    else:
        dataDictionary['version'] = version
        dataDictionary['binary'] = binary
        dataDictionary['defaultScenario'] = context.scene.name
        dataDictionary['cameras'] = collections.OrderedDict()
        dataDictionary['lights'] = collections.OrderedDict()
        dataDictionary['materials'] = collections.OrderedDict()
        dataDictionary['scenarios'] = collections.OrderedDict()
    # Cameras

    cameras = [o for o in bpy.data.objects if o.type == 'CAMERA']

    for i in range(len(cameras)):
        cameraObject = cameras[i]
        camera = cameraObject.data
        if camera.users == 0:
            continue
        if camera.type == "PERSP":
            aperture = camera.gpu_dof.fstop
            if camera.name not in dataDictionary['cameras']:
                dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            if aperture == 128.0:
                cameraType = "pinhole"
                dataDictionary['cameras'][camera.name]['type'] = cameraType
                fov = camera.angle * 180 / 3.141592653589793  # convert rad to degree
                fov = fov * scn.render.resolution_y / scn.render.resolution_x # correct aspect ratio
                dataDictionary['cameras'][camera.name]['fov'] = fov
            else:
                cameraType = "focus"
                dataDictionary['cameras'][camera.name]['type'] = cameraType
                dataDictionary['cameras'][camera.name]['focalLength'] = camera.lens
                dataDictionary['cameras'][camera.name]['chipHeight'] = camera.sensor_height
                dataDictionary['cameras'][camera.name]['focusDistance'] = camera.dof_distance
                dataDictionary['cameras'][camera.name]['aperture'] = aperture
        elif camera.type == "ORTHO":
            if camera.name not in dataDictionary['cameras']:
                dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            cameraType = "ortho"
            dataDictionary['cameras'][camera.name]['type'] = cameraType
            orthoWidth = camera.ortho_scale
            dataDictionary['cameras'][camera.name]['width'] = orthoWidth
            orthoHeight = scn.render.resolution_y / scn.render.resolution_x * orthoWidth  # get aspect ratio via resolution
            dataDictionary['cameras'][camera.name]['height'] = orthoHeight
        else:
            self.report({'WARNING'}, ("Skipping unsupported camera type: \"%s\" from: \"%s\"." % (camera.type, camera.name)))
            continue
        cameraPath = []
        viewDirectionPath = []
        upPath = []
        if camera.animation_data:  # TODO Check if this works
            frameinfo = getframeinfo(currentframe())
            self.report({'WARNING'},
                        "Using not tested code for animated camera: line %d." % frameinfo.lineno)
            x_curve = camera.animation_data.action.fcurves.find('location', index=0)
            y_curve = camera.animation_data.action.fcurves.find('location', index=1)
            z_curve = camera.animation_data.action.fcurves.find('location', index=2)
            for f in range(scn.frame_start, scn.frame_end):
                pos = (x_curve.evaluate(f), y_curve.evaluate(f), z_curve.evaluate(f))
                cameraPath.append(flip_space(pos))
        else:  # for not animated Cameras
            trans, rot, scale = cameraObject.matrix_world.decompose()
            cameraPath.append(flip_space(cameraObject.location))
            viewDirection = rot * Vector((0.0, 0.0, -1.0))
            viewDirectionPath.append(flip_space(viewDirection))
            up = rot * Vector((0.0, 1.0, 0.0))
            upPath.append(flip_space(up))
        dataDictionary['cameras'][camera.name]['path'] = cameraPath
        dataDictionary['cameras'][camera.name]['viewDir'] = viewDirectionPath
        dataDictionary['cameras'][camera.name]['up'] = upPath

    if len(dataDictionary['cameras']) == 0:
        self.report({'ERROR'}, "No camera found.")  # Stop if no camera was exported
        return -1

    # Lights
    lightNames = []

    lamps = [o for o in bpy.data.objects if o.type == 'LAMP']
    for i in range(len(lamps)):
        lampObject = lamps[i]
        lamp = lampObject.data
        if lamp.users == 0:
            continue
        if lamp.type == "POINT":
            lightNames.append(lamp.name)
            if lamp.name not in dataDictionary['lights']:
                dataDictionary['lights'][lamp.name] = collections.OrderedDict()
            if lamp.active_texture is not None:
                lampTextureSlot = lamp.texture_slots[lamp.active_texture_index]
                if lampTextureSlot.texture.type != "IMAGE":
                    self.report({'WARNING'}, ("Skipping goniometric lamp: \"%s\" because Texture: \"%s\" is not an image." % (lamp.name, lampTextureSlot.texture.name)))
                else:
                    if lampTextureSlot.texture.image is None:
                        self.report({'WARNING'}, ("Skipping goniometric lamp: \"%s\" because Texture: \"%s\" has no image." % (lamp.name, lampTextureSlot.texture.name)))
                    else:
                        lightType = "goniometric"
                        dataDictionary['lights'][lamp.name]['type'] = lightType
                        dataDictionary['lights'][lamp.name]['position'] = flip_space(lampObject.location)
                        finalPath = make_path_relative_to_root(lampTextureSlot.texture.image.filepath)
                        dataDictionary['lights'][lamp.name]['map'] = finalPath
                        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
            else:
                lightType = "point"
                dataDictionary['lights'][lamp.name]['type'] = lightType
                dataDictionary['lights'][lamp.name]['position'] = flip_space(lampObject.location)
                dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
                dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
        elif lamp.type == "SUN":
            lightNames.append(lamp.name)
            if lamp.name not in dataDictionary['lights']:
                dataDictionary['lights'][lamp.name] = collections.OrderedDict()
            lightType = "directional"
            dataDictionary['lights'][lamp.name]['type'] = lightType
            viewDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
            dataDictionary['lights'][lamp.name]['direction'] = flip_space(viewDirection)
            dataDictionary['lights'][lamp.name]['radiance'] = [lamp.color.r, lamp.color.g, lamp.color.b]
            dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
        elif lamp.type == "SPOT":
            lightNames.append(lamp.name)
            if lamp.name not in dataDictionary['lights']:
                dataDictionary['lights'][lamp.name] = collections.OrderedDict()
            lightType = "spot"
            dataDictionary['lights'][lamp.name]['type'] = lightType
            dataDictionary['lights'][lamp.name]['position'] = flip_space(lampObject.location)
            lightDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
            dataDictionary['lights'][lamp.name]['direction'] = flip_space(lightDirection)
            dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
            dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
            dataDictionary['lights'][lamp.name]['width'] = lamp.spot_size / 2
            # Try to match the inner circle for the falloff (not exact, blender seems buggy):
            # https://blender.stackexchange.com/questions/39555/how-to-calculate-blend-based-on-spot-size-and-inner-cone-angle
            dataDictionary['lights'][lamp.name]['falloffStart'] = math.atan(math.tan(lamp.spot_size / 2) * math.sqrt(1-lamp.spot_blend))
        else:
            self.report({'WARNING'}, ("Skipping unsupported lamp type: \"%s\" from: \"%s\"." % (lamp.type, lamp.name)))
            continue
    world = scn.world
    worldTextureSlot = world.texture_slots[world.active_texture_index]
    if worldTextureSlot is not None:
        if worldTextureSlot.texture is not None:
            if worldTextureSlot.texture.type != "IMAGE":
                self.report({'WARNING'}, ("Skipping environment map: \"%s\" because it is not an image." % worldTextureSlot.texture.name))
            else:
                if worldTextureSlot.texture.image is None:
                    self.report({'WARNING'}, ("Skipping environment map: \"%s\" because it has no image." % worldTextureSlot.texture.name))
                else:
                    lightNames.append(worldTextureSlot.texture.name)
                    dataDictionary['lights'][worldTextureSlot.texture.name] = collections.OrderedDict()
                    finalPath = make_path_relative_to_root(worldTextureSlot.texture.image.filepath)
                    lightType = "envmap"
                    dataDictionary['lights'][worldTextureSlot.texture.name]["type"] = lightType
                    dataDictionary['lights'][worldTextureSlot.texture.name]["map"] = finalPath
                    dataDictionary['lights'][worldTextureSlot.texture.name]["scale"] = worldTextureSlot.horizon_factor

    # Materials

    # Create dict with used material names
    objects = bpy.data.objects
    materialNames = dict()

    for obj in objects:
        if obj.users != 0:
            if obj.type == "MESH" or "sphere" in obj:
                if obj.material_slots is not None:
                    for materialSlot in obj.material_slots:
                        if materialSlot.material is not None:
                            materialNames[materialSlot.material.name] = True

    materials = bpy.data.materials
    for i in range(len(materials)):
        material = materials[i]
        # if material is not used continue
        if material.name not in materialNames:
            continue
        if material.name not in dataDictionary['materials']:
            dataDictionary['materials'][material.name] = collections.OrderedDict()
        workDictionary = dataDictionary['materials'][material.name]
        # Create texture map which has "property name" -> texture instead of polling, if a texture
        # is used for diffuse...
        textureMap = {}
        for j in range(len( material.texture_slots)):
            if not material.use_textures[j]:
                continue
            textureSlot = material.texture_slots[j]
            if textureSlot is None:
                continue
            if textureSlot.texture.type != "IMAGE":
                self.report({'WARNING'}, (
                            "Skipping image texture \"%s\" from material: \"%s\" because it is not an image." % (textureSlot.texture.name, material.name)))
                continue
            if textureSlot.texture.image is None:
                self.report({'WARNING'}, ("Skipping image texture \"%s\" from material: \"%s\" because it has no image." % (textureSlot.texture.name, material.name)))
                continue
            if textureSlot.use_map_color_diffuse:
                if 'diffuse' in textureMap:
                    self.report({'WARNING'}, ("Too many diffuse textures: \"%s\",\"%s\" from material: \"%s\"." % (material.texture_slots[textureMap['diffuse']].name, textureSlot.name, material.name)))
                else:
                    textureMap['diffuse'] = j
            if textureSlot.use_map_color_spec:
                if 'specular' in textureMap:
                    self.report({'WARNING'}, ("Too many specular textures: \"%s\",\"%s\" from material: \"%s\"." % (material.texture_slots[textureMap['specular']].name, textureSlot.name, material.name)))
                else:
                    textureMap['specular'] = j
            if textureSlot.use_map_emit:
                if 'emissive' in textureMap:
                    self.report({'WARNING'}, ("Too many emissive textures: \"%s\",\"%s\" from material: \"%s\"." % (material.texture_slots[textureMap['emissive']].name, textureSlot.name, material.name)))
                else:
                    textureMap['emissive'] = j
            if textureSlot.use_map_hardness:
                if 'roughness' in textureMap:
                    self.report({'WARNING'}, ("Too many roughness textures: \"%s\",\"%s\" from material: \"%s\"." % (material.texture_slots[textureMap['roughness']].name, textureSlot.name, material.name)))
                else:
                    textureMap['roughness'] = j

        # Check for Multi-Layer material
        hasRefraction = material.use_transparency
        hasReflection = material.specular_intensity > 0
        hasDiffuse = material.diffuse_intensity > 0 and (material.diffuse_color.r > 0
                                                      or material.diffuse_color.g > 0
                                                      or material.diffuse_color.b > 0)
        hasEmission = material.emit > 0
        useFresnel = (material.diffuse_shader == "FRESNEL") or (material.raytrace_transparency.fresnel > 0) # TODO: more options to enable fresnel?
        applyDiffuseScale = True # Conditional replaced later if intensity is used as a blend factor

        # Check which combination is given and export the appropriate material
        if hasEmission: 
            # Always blend emission additive
            if hasDiffuse or hasReflection or hasRefraction:
                write_blend_material(workDictionary, textureMap, material, 1.0, 1.0)
                write_emissive_material(workDictionary['layerA'], textureMap, material)
                workDictionary = workDictionary['layerB']
            else: # No blending (emission is the only layer)
                write_emissive_material(workDictionary, textureMap, material)

        if hasReflection:
            # Blend with fresnel or constant value
            if hasRefraction or hasDiffuse:
                if useFresnel:
                    write_fresnel_material(workDictionary, textureMap, material)
                    write_torrance_material(workDictionary['layerReflection'], textureMap, material, True, self)
                    workDictionary = workDictionary['layerRefraction']
                    # TODO: export glass instead
                else:
                    factorB = 1-material.specular_intensity if hasRefraction else material.diffuse_intensity
                    applyDiffuseScale = False # in both cases: if Refr+Diffuse the diffuse_intensity will also be used as blend factor.
                    write_blend_material(workDictionary, textureMap, material, material.specular_intensity, factorB)
                    write_torrance_material(workDictionary['layerA'], textureMap, material, False, self)
                    workDictionary = workDictionary['layerB']
            else: # No further layers/blending
                if useFresnel:  # Use a second empty layer in fresnel
                    write_fresnel_material(workDictionary, textureMap, material)
                    write_torrance_material(workDictionary['layerReflection'], textureMap, material, True, self)
                    workDictionary['layerRefraction']['type'] = "lambert"
                    workDictionary['layerRefraction']['albedo'] = [0,0,0]
                else:
                    write_torrance_material(workDictionary, textureMap, material, True, self)

        if hasRefraction:
            if hasDiffuse: # one last blending necessary
                write_blend_material(workDictionary, textureMap, material, 1-material.diffuse_intensity, material.diffuse_intensity)
                write_walter_material(workDictionary['layerA'], textureMap, material)
                workDictionary = workDictionary['layerB']
                applyDiffuseScale = False
            else:
                write_walter_material(workDictionary, textureMap, material)

        if hasDiffuse:
            if material.diffuse_shader == "OREN_NAYAR":
                write_orennayar_material(workDictionary, textureMap, material, applyDiffuseScale)
            if not (material.diffuse_shader == "LAMBERT" or material.diffuse_shader == "FRESNEL"):
                self.report({'WARNING'}, ("Unsupported diffuse material: \"%s\". Exporting as Lambert material." % (material.name)))
            write_lambert_material(workDictionary, textureMap, material, applyDiffuseScale)

    # Scenarios

    # Make a dict with all instances
    if use_selection:
        tempObjects = bpy.context.selected_objects
    else:
        tempObjects = bpy.data.objects

    objects = []
    for obj in tempObjects:
        if obj.users != 0:
            objects.append(obj)

    usedMeshes = []
    for i in range(len(objects)):
        currentObject = objects[i]
        if currentObject.type != "MESH" and "sphere" not in currentObject:  # If mesh or sphere
            continue
        if currentObject.data in usedMeshes:
            continue
        if len(currentObject.lod_levels) != 0:  # if object has LOD levels
            if currentObject.type != "MESH" and "sphere" in currentObject:  # skip object with data doesnt work for meshes
                tempMesh = currentObject.to_mesh(bpy.context.scene, True, calc_tessface=False,
                                                 settings='RENDER')  # excepts if object has no geometry and is mesh but returns None for none Mesh
                if tempMesh is not None:
                    if len(currentObject.data.vertices) != 0:
                        bpy.data.meshes.remove(tempMesh)
                        continue
            elif len(
                    currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them) works only for meshes
                continue
        usedMeshes.append(currentObject.data)

    instances = []
    for i in range(len(objects)):
        currentObject = objects[i]
        if currentObject.type != "MESH" and "sphere" not in currentObject:
            continue
        if currentObject.type != "MESH" and "sphere" in currentObject:  # doesnt work for meshes
            tempMesh = currentObject.to_mesh(bpy.context.scene, True, calc_tessface=False,
                                             settings='RENDER')  # excepts if object has no geometry and is mesh but returns None for none Mesh
            if tempMesh is None:
                continue
            bpy.data.meshes.remove(tempMesh)
        if len(currentObject.lod_levels) != 0:  # if object has LOD levels
            if not hasattr(currentObject.data, 'vertices'):
                continue
            if len(
                    currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them)
                continue
        if currentObject.data in usedMeshes:
            instances.append(currentObject.name)

    for scene in bpy.data.scenes:
        if scene.name not in dataDictionary['scenarios']:
            dataDictionary['scenarios'][scene.name] = collections.OrderedDict()
        cameraName = ''  # type: str
        if hasattr(scene.camera, 'name'):
            if scene.camera.data.type == "PERSP" or scene.camera.data.type == "ORTHO":
                cameraName = scene.camera.name
            else:
                cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary
                # dataDictionary['cameras'] has at least 1 element otherwise the program would have exited earlier
        else:
            cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary

        dataDictionary['scenarios'][scene.name]['camera'] = cameraName
        dataDictionary['scenarios'][scene.name]['resolution'] = [scene.render.resolution_x, scene.render.resolution_y]

        dataDictionary['scenarios'][scene.name]['lights'] = lightNames
        dataDictionary['scenarios'][scene.name]['lod'] = 0
        # Material assignments
        if 'materialAssignments' not in dataDictionary['scenarios'][scene.name]:
            dataDictionary['scenarios'][scene.name]['materialAssignments'] = collections.OrderedDict()
        for material in dataDictionary['materials']:
            dataDictionary['scenarios'][scene.name]['materialAssignments'][material] = material

        # Object properties
        if 'objectProperties' not in dataDictionary['scenarios'][scene.name]:
            dataDictionary['scenarios'][scene.name]['objectProperties'] = collections.OrderedDict()

        # Instance properties
        if 'instanceProperties' not in dataDictionary['scenarios'][scene.name]:
            dataDictionary['scenarios'][scene.name]['instanceProperties'] = collections.OrderedDict()

        sceneObjectNames = []

        for object in scene.objects:
            sceneObjectNames.append(object.name)

        for instance in instances:
            if instance not in sceneObjectNames:
                if instance not in dataDictionary['scenarios'][scene.name]['instanceProperties']:
                    dataDictionary['scenarios'][scene.name]['instanceProperties'][instance] = collections.OrderedDict()
                dataDictionary['scenarios'][scene.name]['instanceProperties'][instance]['mask'] = True


    # CustomJSONEncoder: Custom float formater (default values will be like 0.2799999994039535)
    # and which packs small arrays in one line
    dump = json.dumps(dataDictionary, indent=4, cls=CustomJSONEncoder)
    file = open(filepath, 'w')
    file.write(dump)
    file.close()
    return 0



#   ###   #  #   #     #     ###   #   #
#   #  #  #  ##  #    # #    #  #   # #
#   ###   #  # # #   #   #   ###     #
#   #  #  #  #  ##  #######  #  #    #
#   ###   #  #   #  #     #  #  #    #

def export_binary(context, self, filepath, use_selection, use_deflation, use_compression):
    scn = context.scene
    # Binary
    binary = bytearray()
    # Materials Header
    binary.extend("Mats".encode())
    materials = []
    materialNames = []
    materialNameLengths = []

    materialNamesDict = dict()

    for obj in bpy.data.objects:
        if obj.users != 0:
            if obj.type == "MESH" or "sphere" in obj:
                if obj.material_slots is not None:
                    for materialSlot in obj.material_slots:
                        if materialSlot.material is not None:
                            if materialSlot.material.name not in materialNamesDict:
                                materialNamesDict[materialSlot.material.name] = True
                                materials.append(materialSlot.material)

    materialLookup = collections.OrderedDict()  # Lookup for the polygons
    bytePosition = 16
    if len(materials) == 0:
        self.report({'ERROR'}, ("There are no materials in the scene."))
        return -1
    for i in range(len(materials)):
        material = materials[i]
        materialLookup[material.name] = i
        materialNames.append(material.name.encode())
        materialNameLength = len(material.name.encode())
        materialNameLengths.append(materialNameLength.to_bytes(4, byteorder='little'))
        bytePosition += 4 + materialNameLength

    binary.extend(bytePosition.to_bytes(8, byteorder='little'))
    binary.extend(len(materials).to_bytes(4, byteorder='little'))
    for i in range(len(materialNameLengths)):
        binary.extend(materialNameLengths[i])
        binary.extend(materialNames[i])

    # Objects Header

    # Type
    binary.extend("Objs".encode())
    instanceSectionStartBinaryPosition = len(binary)  # Save Position in binary has to be corrected later
    # Next section start position
    binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known
    # Global object flags (compression etc.)
    flags = 0x0
    if use_deflation:
        flags |= 1
    if use_compression:
        flags |= 2
    binary.extend(flags.to_bytes(4, byteorder='little'))

    countOfObjects = 0
    if use_selection:
        tempObjects = bpy.context.selected_objects
    else:
        tempObjects = bpy.data.objects

    objects = []
    for obj in tempObjects:
        if obj.users != 0:
            objects.append(obj)

    usedMeshes = []
    for i in range(len(objects)):
        currentObject = objects[i]
        if currentObject.type != "MESH" and "sphere" not in currentObject:  # If mesh or sphere
            continue
        if currentObject.data in usedMeshes:
            continue
        if len(currentObject.lod_levels) != 0:  # if object has LOD levels
            if currentObject.type != "MESH" and "sphere" in currentObject:  # skip object with data doesnt work for meshes
                tempMesh = currentObject.to_mesh(bpy.context.scene, True, calc_tessface=False, settings='RENDER')  # excepts if object has no geometry and is mesh but returns None for none Mesh
                if tempMesh is not None:
                    if len(currentObject.data.vertices) != 0:
                        bpy.data.meshes.remove(tempMesh)
                        continue
            elif len(currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them) works only for meshes
                continue
        usedMeshes.append(currentObject.data)
        countOfObjects += 1

    binary.extend(countOfObjects.to_bytes(4, byteorder='little'))

    objectStartBinaryPosition = []  # Save Position in binary to set this correct later
    for i in range(countOfObjects):
        objectStartBinaryPosition.append(len(binary))
        binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known

    currentObjectNumber = 0
    usedMeshes = []
    activeObject = scn.objects.active
    for i in range(len(objects)):
        currentObject = objects[i]
        if currentObject.type != "MESH" and "sphere" not in currentObject:  # If mesh or sphere
            continue
        if currentObject.data in usedMeshes:
            continue
        if len(currentObject.lod_levels) != 0:  # if object has LOD levels
            if currentObject.type != "MESH" and "sphere" in currentObject:  # skip object with data doesnt work for meshes
                tempMesh = currentObject.to_mesh(bpy.context.scene, True, calc_tessface=False, settings='RENDER')  # excepts if object has no geometry and is mesh but returns None for none Mesh
                if tempMesh is not None:
                    if len(currentObject.data.vertices) != 0:
                        bpy.data.meshes.remove(tempMesh)
                        continue
            elif len(currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them) works only for meshes
                continue
        usedMeshes.append(currentObject.data)
        objectStartPosition = len(binary).to_bytes(8, byteorder='little')
        for j in range(8):
            binary[objectStartBinaryPosition[currentObjectNumber]+j] = objectStartPosition[j]
        currentObjectNumber += 1
        # Type check
        binary.extend("Obj_".encode())
        # Object name
        objectName = currentObject.data.name.encode()
        objectNameLength = len(objectName)
        binary.extend(objectNameLength.to_bytes(4, byteorder='little'))
        binary.extend(objectName)
        # Write the object flags (NOT compression/deflation, but rather isEmissive etc)
        objectFlagsBinaryPosition = len(binary)
        objectFlags = 0
        binary.extend(objectFlags.to_bytes(4, byteorder='little'))
        # keyframe
        binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO keyframes
        # OBJID of previous object in animation
        binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO keyframes
        # Bounding box

        # Calculate Lod chain to get bounding box over all Lods

        lodLevels = []
        lodChainStart = 0
        if len(currentObject.lod_levels) != 0:  # if it has Lod levels check depth of Lod levels
            lodObject = currentObject.lod_levels[1].object
            maxDistance = -1
            while lodObject not in lodLevels:
                lodLevels.append(lodObject)
                if len(lodObject.lod_levels) == 0:
                    lodLevels = []
                    lodChainStart = 0
                    self.report({'WARNING'}, (
                                "Skipped LOD levels from: \"%s\" because LOD Object: \"%s\" has no successor." % (
                        currentObject.name, lodObject.name)))
                    break
                if maxDistance < lodObject.lod_levels[1].distance:
                    maxDistance = lodObject.lod_levels[1].distance  # the last LOD level has the highest distance
                    lodChainStart = len(lodLevels) - 1
                lodObject = lodObject.lod_levels[1].object
        if len(lodLevels) == 0:
            lodLevels.append(currentObject)  # if no LOD levels the object itself ist the only LOD level

        boundingBox = lodLevels[0].bound_box

        boundingBoxMin = [boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]]
        boundingBoxMax = [boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]]

        for lodObject in lodLevels:

            boundingBox = lodObject.bound_box
            # Set bounding box min/max to last element of the bb
            # boundingBoxMin = flip_space((boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]))
            # boundingBoxMax = boundingBoxMin.copy()

            for j in range(7):  # 0-6
                corner = [ boundingBox[j][0], boundingBox[j][1], boundingBox[j][2] ]
                for k in range(3):  # x y z
                    if corner[k] < boundingBoxMin[k]:
                        boundingBoxMin[k] = corner[k]
                    if corner[k] > boundingBoxMax[k]:
                        boundingBoxMax[k] = corner[k]
        binary.extend(struct.pack('<3f', *boundingBoxMin))    # '<' = little endian  3 = 3 times f  'f' = float32
        binary.extend(struct.pack('<3f', *boundingBoxMax))
        # <Jump Table> LOD

        # Number of entries in table
        binary.extend((len(lodLevels)).to_bytes(4, byteorder='little'))
        lodStartBinaryPosition = len(binary)
        for j in range(len(lodLevels)):
            binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known
        for j in range(len(lodLevels)):
            lodObject = lodLevels[(lodChainStart+j) % len(lodLevels)]  # for the correct starting object
            # start Positions
            lodStartPosition = len(binary).to_bytes(8, byteorder='little')
            for k in range(8):
                binary[lodStartBinaryPosition + k + j*8] = lodStartPosition[k]
            # Type
            binary.extend("LOD_".encode())
            scn.objects.active = lodObject
            mode = context.active_object.mode
            bpy.ops.object.mode_set(mode='EDIT')
            if "sphere" not in lodObject:
                mesh = lodObject.to_mesh(scn, True, calc_tessface=False, settings='RENDER')  # applies all modifiers
                bm = bmesh.new()
                bm.from_mesh(mesh)  # bmesh gives a local editable mesh copy
                faces = bm.faces
                faces.ensure_lookup_table()
                facesToTriangulate = []
                for k in range(len(faces)):
                    if len(faces[k].edges) > 4:
                        facesToTriangulate.append(faces[k])
                bmesh.ops.triangulate(bm, faces=facesToTriangulate[:], quad_method=0, ngon_method=0)
                # Split vertices if vertex has multiple uv coordinates (is used in multiple triangles)
                # or if it is not smooth
                if 'uv_layers' in lodObject.data and len(lodObject.data.uv_layers):
                    # mark seams from uv islands
                    bpy.ops.uv.seams_from_islands()
                edgesToSplit = [e for e in bm.edges if e.seam or not e.smooth
                    or not all(f.smooth for f in e.link_faces)]
                bmesh.ops.split_edges(bm, edges=edgesToSplit)

                faces = bm.faces  # update faces
                faces.ensure_lookup_table()
                numberOfTriangles = 0
                numberOfQuads = 0
                for k in range(len(faces)):
                    numVertices = len(faces[k].verts)
                    if numVertices == 3:
                        numberOfTriangles += 1
                    elif numVertices == 4:
                        numberOfQuads += 1
                    else:
                        self.report({'ERROR'}, ("%d Vertices in face from object: \"%s\"." % (numVertices, lodObject.name)))
                        return
                bm.to_mesh(mesh)
                bm.free()
                numberOfSpheres = 0
                numberOfVertices = len(mesh.vertices)
                numberOfEdges = len(mesh.edges)
                numberOfVertexAttributes = 0
                numberOfFaceAttributes = 0  # TODO Face Attributes
                numberOfSphereAttributes = 0
            else:  # Change Values if it is an sphere
                numberOfTriangles = 0
                numberOfQuads = 0
                numberOfSpheres = 1
                numberOfVertices = 0
                numberOfEdges = 0
                numberOfVertexAttributes = 0
                numberOfFaceAttributes = 0
                numberOfSphereAttributes = 0  # TODO Sphere Attributes

            # Number of Triangles
            binary.extend(numberOfTriangles.to_bytes(4, byteorder='little'))
            # Number of Quads
            binary.extend(numberOfQuads.to_bytes(4, byteorder='little'))
            # Number of Spheres
            binary.extend(numberOfSpheres.to_bytes(4, byteorder='little'))
            # Number of Vertices
            binary.extend(numberOfVertices.to_bytes(4, byteorder='little'))
            # Number of Edges
            binary.extend(numberOfEdges.to_bytes(4, byteorder='little'))
            # Number of Vertex Attributes
            numberOfVertexAttributesBinaryPosition = len(binary)  # has to be corrected when value is known
            binary.extend(numberOfVertexAttributes.to_bytes(4, byteorder='little'))
            # Number of Face Attributes
            binary.extend(numberOfFaceAttributes.to_bytes(4, byteorder='little'))
            # Number of Sphere Attributes
            binary.extend(numberOfSphereAttributes.to_bytes(4, byteorder='little'))
            if "sphere" not in lodObject:
                # Vertex data
                vertices = mesh.vertices
                mesh.calc_normals()
                if mesh.has_custom_normals:
                    mesh.calc_normals_split()
                uvCoordinates = numpy.empty(len(vertices), dtype=object)
                if len(mesh.uv_layers) == 0:
                    self.report({'WARNING'}, ("LOD Object: \"%s\" has no uv layers." % (lodObject.name)))
                    for k in range(len(uvCoordinates)):
                        theta = math.acos(vertices[k].co[1]/(math.sqrt((vertices[k].co[0]*vertices[k].co[0]) + (vertices[k].co[1]*vertices[k].co[1]) + (vertices[k].co[2]*vertices[k].co[2]))))
                        phi = numpy.arctan2(vertices[k].co[2], vertices[k].co[0])
                        if theta < 0:
                            theta += math.pi
                        if(phi < 0):
                            phi += 2*math.pi
                        u = theta / math.pi
                        v = phi / (2*math.pi)
                        uvCoordinates[k] = [u, v]
                else:
                    uv_layer = mesh.uv_layers[0]
                    for polygon in mesh.polygons:
                        for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                            uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer.data[loop_index].uv
                vertexDataArray = bytearray()  # Used for deflation
                for k in range(len(vertices)):
                    vertexDataArray.extend(struct.pack('<3f', *mesh.vertices[k].co))
                write_vertex_normals(vertexDataArray, mesh, use_compression)
                for k in range(len(vertices)):
                    vertexDataArray.extend(struct.pack('<2f', uvCoordinates[k][0], uvCoordinates[k][1]))
                vertexOutData = vertexDataArray
                if use_deflation and len(vertexDataArray) > 0:
                    vertexOutData = zlib.compress(vertexDataArray, 8)
                    binary.extend(len(vertexOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(vertexDataArray).to_bytes(4, byteorder='little'))
                binary.extend(vertexOutData)

                # Vertex Attributes
                vertexAttributesDataArray = bytearray()  # Used for deflation
                for uvNumber in range(len(mesh.uv_layers)):  # get Number of Vertex Attributes
                    if uvNumber == 0:
                        continue
                    uv_layer = mesh.uv_layers[uvNumber]
                    if not uv_layer:
                        continue
                    numberOfVertexAttributes += 1
                for colorNumber in range(len(mesh.vertex_colors)):
                    vertex_color = mesh.vertex_colors[colorNumber]
                    if not vertex_color:
                        continue
                    numberOfVertexAttributes += 1

                if numberOfVertexAttributes > 0:
                    numberOfVertexAttributesBin = numberOfVertexAttributes.to_bytes(4, byteorder='little')
                    for k in range(4):
                        binary[numberOfVertexAttributesBinaryPosition + k] = numberOfVertexAttributesBin[k]

                    binary.extend("Attr".encode())
                    for uvNumber in range(len(mesh.uv_layers)):
                        if uvNumber == 0:
                            continue
                        uvCoordinates = numpy.empty(len(vertices), dtype=object)
                        uv_layer = mesh.uv_layers[uvNumber]
                        if not uv_layer:
                            continue
                        uvName = uv_layer.name
                        vertexAttributesDataArray.extend(len(uvName.encode()).to_bytes(4, byteorder='little'))
                        vertexAttributesDataArray.extend(uvName.encode())
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((16).to_bytes(4, byteorder='little'))  # Type: 2*f32 (16)
                        vertexAttributesDataArray.extend((len(vertices)*4*2).to_bytes(8, byteorder='little'))  # Count of Bytes len(vertices) * 4(f32) * 2(uvCoordinates per vertex)
                        for polygon in mesh.polygons:
                            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                                uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer.data[loop_index].uv
                        for k in range(len(vertices)):
                            vertexAttributesDataArray.extend(struct.pack('<2f', uvCoordinates[k][0], uvCoordinates[k][1]))
                    for colorNumber in range(len(mesh.vertex_colors)):
                        vertexColor = numpy.empty(len(vertices), dtype=object)
                        vertex_color_layer = mesh.vertex_colors[colorNumber]
                        if not vertex_color_layer:
                            continue
                        colorName = vertex_color_layer.name
                        vertexAttributesDataArray.extend(len(colorName.encode()).to_bytes(4, byteorder='little'))
                        vertexAttributesDataArray.extend(colorName.encode())
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((0).to_bytes(4, byteorder='little'))  # No meta information
                        vertexAttributesDataArray.extend((17).to_bytes(4, byteorder='little'))  # Type: 3*f32 (17)
                        vertexAttributesDataArray.extend((len(vertices)*4*3).to_bytes(8, byteorder='little'))  # Count of Bytes len(vertices) * 4(f32) * 3(color channels per vertex)
                        for polygon in mesh.polygons:
                            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                                vertexColor[mesh.loops[loop_index].vertex_index] = vertex_color_layer.data[loop_index].color
                        for k in range(len(vertices)):
                            vertexAttributesDataArray.extend(struct.pack('<3f', vertexColor[k][0], vertexColor[k][1], vertexColor[k][2]))

                vertexAttributesOutData = vertexAttributesDataArray
                if use_deflation and len(vertexAttributesDataArray) > 0:
                    vertexAttributesOutData = zlib.compress(vertexAttributesDataArray, 8)
                    binary.extend(len(vertexAttributesOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(vertexAttributesDataArray).to_bytes(4, byteorder='little'))
                binary.extend(vertexAttributesOutData)

                # TODO more Vertex Attributes? (with deflation)
                # Triangles
                triangleDataArray = bytearray()  # Used for deflation
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 3:
                        for k in range(3):
                            triangleDataArray.extend(polygon.vertices[k].to_bytes(4, byteorder='little'))
                triangleOutData = triangleDataArray
                if use_deflation and len(triangleDataArray) > 0:
                    triangleOutData = zlib.compress(triangleDataArray, 8)
                    binary.extend(len(triangleOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(triangleDataArray).to_bytes(4, byteorder='little'))
                binary.extend(triangleOutData)
                # Quads
                quadDataArray = bytearray()  # Used for deflation
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 4:
                        for k in range(4):
                            quadDataArray.extend(polygon.vertices[k].to_bytes(4, byteorder='little'))
                quadOutData = quadDataArray
                if use_deflation and len(quadDataArray) > 0:
                    quadOutData = zlib.compress(quadDataArray, 8)
                    binary.extend(len(quadOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(quadDataArray).to_bytes(4, byteorder='little'))
                binary.extend(quadOutData)
                # Material IDs
                matIDDataArray = bytearray()
                if len(mesh.materials) == 0:
                    self.report({'WARNING'}, ("LOD Object: \"%s\" has no materials." % (lodObject.name)))
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 3:
                        if len(mesh.materials) != 0:
                            matIDDataArray.extend(materialLookup[mesh.materials[polygon.material_index].name].to_bytes(2, byteorder='little'))
                        else:
                            matIDDataArray.extend((0).to_bytes(2, byteorder='little'))
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 4:
                        if len(mesh.materials) != 0:
                            matIDDataArray.extend(materialLookup[mesh.materials[polygon.material_index].name].to_bytes(2, byteorder='little'))
                        else:
                            matIDDataArray.extend((0).to_bytes(2, byteorder='little')) # first material is default when the object has no mats
                if (objectFlags & 1) == 0:
                    if len(mesh.materials) != 0:
                        for polygon in mesh.polygons:
                            if len(mesh.materials) != 0:
                                if mesh.materials[polygon.material_index].emit > 0.0:
                                    objectFlags |= 1
                                    objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
                                    for k in range(4):
                                        binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]
                                    break
                else:
                    if materials[0] > 0.0:  # first material is default when the object has no mats
                        objectFlags |= 1
                        objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
                        for k in range(4):
                            binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]
                matIDOutData = matIDDataArray
                if use_deflation and len(matIDDataArray) > 0:
                    matIDOutData = zlib.compress(matIDDataArray, 8)
                    binary.extend(len(matIDOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(matIDDataArray).to_bytes(4, byteorder='little'))
                binary.extend(matIDOutData)
                # Face Attributes
                # TODO Face Attributes (with deflation)

                bpy.data.meshes.remove(mesh)
            else:
                # Spheres
                sphereDataArray = bytearray()
                center = ( (boundingBoxMin[0] + boundingBoxMax[0]) / 2,
                           (boundingBoxMin[1] + boundingBoxMax[1]) / 2,
                           (boundingBoxMin[2] + boundingBoxMax[2]) / 2 )
                radius = abs(boundingBoxMin[0]-center[0])
                sphereDataArray.extend(struct.pack('<3f', *center))
                sphereDataArray.extend(struct.pack('<f', radius))

                if lodObject.active_material is not None:
                    sphereDataArray.extend(materialLookup[lodObject.active_material.name].to_bytes(2, byteorder='little'))
                    if (objectFlags & 1) == 0:
                        if lodObject.active_material.emit > 0.0:
                            objectFlags |= 1
                            objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
                            for k in range(4):
                                binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]
                else:

                    self.report({'WARNING'}, ("LOD Object: \"%s\" has no materials." % (lodObject.name)))
                    sphereDataArray.extend((0).to_bytes(2, byteorder='little'))  # first material is default when the object has no mats

                sphereOutData = sphereDataArray
                # TODO Sphere Attributes
                if use_deflation and len(sphereDataArray) > 0:
                    sphereOutData = zlib.compress(sphereDataArray, 8)
                    binary.extend(len(sphereOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(sphereDataArray).to_bytes(4, byteorder='little'))
                binary.extend(sphereOutData)
            # reset used mode
            bpy.ops.object.mode_set(mode=mode)
    #reset active object
    scn.objects.active = activeObject
    # Instances
    instanceStartPosition = len(binary).to_bytes(8, byteorder='little')
    for i in range(8):
        binary[instanceSectionStartBinaryPosition + i] = instanceStartPosition[i]
    # Type
    binary.extend("Inst".encode())
    # Number of Instances
    numberOfInstancesBinaryPosition = len(binary)
    binary.extend((0).to_bytes(4, byteorder='little'))  # has to be corrected later
    numberOfInstances = 0
    for i in range(len(objects)):
        currentObject = objects[i]
        if currentObject.type != "MESH" and "sphere" not in currentObject:
            continue
        if currentObject.type != "MESH" and "sphere" in currentObject:  # doesnt work for meshes
            tempMesh = currentObject.to_mesh(bpy.context.scene, True, calc_tessface=False, settings='RENDER')  # excepts if object has no geometry and is mesh but returns None for none Mesh
            if tempMesh is None:
                continue
            bpy.data.meshes.remove(tempMesh)
        if len(currentObject.lod_levels) != 0:  # if object has LOD levels
            if not hasattr(currentObject.data, 'vertices'):
                continue
            if len(currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them)
                continue
        if currentObject.data in usedMeshes:
            index = usedMeshes.index(currentObject.data)
            binary.extend(len(currentObject.name.encode()).to_bytes(4, byteorder='little'))
            binary.extend(currentObject.name.encode())
            binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
            binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Keyframe
            binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
            transformMat = currentObject.matrix_world
            # Apply the flip_space transformation on instance transformation level.
            binary.extend(struct.pack('<4f', *transformMat[0]))
            binary.extend(struct.pack('<4f', *transformMat[2]))
            for k in range(4):
                binary.extend(struct.pack('<f', -transformMat[1][k]))
            numberOfInstances += 1
    numberOfInstancesBytes = numberOfInstances.to_bytes(4, byteorder='little')
    for i in range(4):
        binary[numberOfInstancesBinaryPosition + i] = numberOfInstancesBytes[i]
    # Write binary to file
    binFile = open(filepath, 'bw')
    binFile.write(binary)
    binFile.close()
    return 0


def export_mufflon(context, self, filepath, use_selection, use_compression,
                   use_deflation, overwrite_default_scenario):
    filename = os.path.splitext(filepath)[0]
    binfilepath = filename + ".mff"
    if export_json(context, self, filepath, binfilepath, use_selection, overwrite_default_scenario) == 0:
        print("Succeeded exporting JSON")
        if export_binary(context, self, binfilepath, use_selection,
                         use_compression, use_deflation) == 0:
            print("Succeeded exporting binary")
        else:
            print("Failed exporting binary")
            print("Stopped exporting")
            return {'CANCELLED'}
    else:
        print("Failed exporting JSON")
        print("Stopped exporting")
        return {'CANCELLED'}
    return {'FINISHED'}

def pack_normal32(vec3):
    l1norm = abs(vec3[0]) + abs(vec3[1]) + abs(vec3[2])
    if vec3[2] >= 0:
        u = vec3[0] / l1norm
        v = vec3[1] / l1norm
    else:  # warp lower hemisphere
        u = (1 - abs(vec3[1]) / l1norm) * (1 if vec3[0] >= 0 else -1)
        v = (1 - abs(vec3[0]) / l1norm) * (1 if vec3[1] >= 0 else -1)
    u = math.floor(u * 32767.0 + 0.5)  # from [-1,1] to [-2^15,2^15-1]
    v = math.floor(v * 32767.0 + 0.5)  # from [-1,1] to [-2^15,2^15-1]
    return u | (v << 16)

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import (ExportHelper, path_reference_mode)
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator


class MufflonExporter(Operator, ExportHelper):
    """This appears in the tooltip of the operator and in the generated docs"""
    bl_idname = "mufflon.exporter"  # important since its how bpy.ops.import_test.some_data is constructed
    bl_label = "Export Mufflon Scene"

    # ExportHelper mixin class uses this
    filename_ext = ".json"
    filter_glob = StringProperty(
            default="*.json;*.mff",
            options={'HIDDEN'},
            maxlen=255,  # Max internal buffer length, longer would be clamped.
            )

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling.
    use_selection = BoolProperty(
            name="Selection Only",
            description="Export selected objects only",
            default=True,
            )
    use_compression = BoolProperty(
            name="Compress normals",
            description="Apply compression to vertex normals (Octahedral)",
            default=False,
            )
    use_deflation = BoolProperty(
            name="Deflate",
            description="Use deflation to reduce file size",
            default=False,
            )
    overwrite_default_scenario = BoolProperty(
            name="Overwrite default scenario",
            description="Overwrite the default scenario when exporting JSON if already set",
            default=True,
            )
    path_mode = path_reference_mode

    def execute(self, context):
        return export_mufflon(context, self, self.filepath, self.use_selection,
                              self.use_deflation, self.use_compression,
                              self.overwrite_default_scenario)


# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
    self.layout.operator(MufflonExporter.bl_idname, text="Mufflon (.json/.mff)")


def register():
    bpy.utils.register_class(MufflonExporter)
    bpy.types.INFO_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(MufflonExporter)
    bpy.types.INFO_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
    register()
