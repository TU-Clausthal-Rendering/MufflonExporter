import bpy
import bmesh
from mathutils import Vector
import os
import json
import collections
import struct
import numpy
import math
import zlib
from collections import OrderedDict
import re

bl_info = {
    "name": "Mufflon Exporter",
    "description": "Exporter for the custom Mufflon file format",
    "author": "Marvin",
    "version": (1, 0),
    "blender": (2, 69, 0),
    "location": "File > Export > Mufflon (.json/.mff)",
    "category": "Import-Export"
}

def export_json(context, self, filepath, binfilepath):
    version = "1.0"
    binary = os.path.relpath(binfilepath, os.path.commonpath([filepath, binfilepath]))

    scn = context.scene
    oldData = collections.OrderedDict()
    dataDictionary = collections.OrderedDict()
    dataDictionary['version'] = version
    dataDictionary['binary'] = binary
    dataDictionary['cameras'] = collections.OrderedDict()
    dataDictionary['lights'] = collections.OrderedDict()
    dataDictionary['materials'] = collections.OrderedDict()
    dataDictionary['scenarios'] = collections.OrderedDict()
    if os.path.isfile(filepath):
        file = open(filepath, 'r')
        jsonStr = file.read()
        file.close()
        try:
            oldData = json.loads(jsonStr, object_pairs_hook=OrderedDict)  # loads the old json and preserves ordering
        except json.decoder.JSONDecodeError as e:
            self.report({'ERROR'}, "Old JSON has wrong format: " + str(e))
            return -1
        dataDictionary['cameras'] = oldData['cameras']
        dataDictionary['lights'] = oldData['lights']
        dataDictionary['materials'] = oldData['materials']
        dataDictionary['scenarios'] = oldData['scenarios']

    # Cameras

    cameras = [o for o in bpy.data.objects if o.type == 'CAMERA']

    for i in range(len(cameras)):
        cameraObject = cameras[i]
        camera = cameraObject.data
        if camera.type == "PERSP":
            aperture = camera.gpu_dof.fstop
            if camera.name not in dataDictionary['cameras']:
                dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            if aperture == 128.0:
                cameraType = "pinhole"
                dataDictionary['cameras'][camera.name]['type'] = cameraType
                fov = camera.angle * 180 / 3.141592653589793  # convert rad to degree
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
            x_curve = camera.animation_data.action.fcurves.find('location', index=0)
            y_curve = camera.animation_data.action.fcurves.find('location', index=1)
            z_curve = camera.animation_data.action.fcurves.find('location', index=2)
            for f in range(scn.frame_start, scn.frame_end):
                x_pos = x_curve.evaluate(f)
                y_pos = y_curve.evaluate(f)
                z_pos = z_curve.evaluate(f)
                cameraPath.append([x_pos, z_pos , y_pos])
        else:  # for not animated Cameras
            cameraPath.append([cameraObject.location.x, cameraObject.location.z, cameraObject.location.y])
            viewDirection = cameraObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
            viewDirectionPath.append([viewDirection.x, viewDirection.z, viewDirection.y])
            up = cameraObject.matrix_world.to_quaternion() * Vector((0.0, 1.0, 0.0))
            upPath.append([up.x, up.z, up.y])
        dataDictionary['cameras'][camera.name]['path'] = cameraPath
        dataDictionary['cameras'][camera.name]['viewDir'] = viewDirectionPath
        dataDictionary['cameras'][camera.name]['up'] = upPath

    if len(dataDictionary['cameras']) == 0:
        self.report({'ERROR'}, "No camera found.")  # Stop if no camera was exported
        return -1

    # Lights

    lamps = [o for o in bpy.data.objects if o.type == 'LAMP']
    for i in range(len(lamps)):
        lampObject = lamps[i]
        lamp = lampObject.data
        if lamp.type == "POINT":
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
                        dataDictionary['lights'][lamp.name]['position'] = [lampObject.location.x, lampObject.location.z, lampObject.location.y]
                        absPath = bpy.path.abspath(lampTextureSlot.texture.image.filepath)
                        finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                        finalPath = finalPath.replace("\\", "/")
                        dataDictionary['lights'][lamp.name]['map'] = finalPath
                        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
            else:
                lightType = "point"
                dataDictionary['lights'][lamp.name]['type'] = lightType
                dataDictionary['lights'][lamp.name]['position'] = [lampObject.location.x, lampObject.location.z, lampObject.location.y]
                dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
                dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
        elif lamp.type == "SUN":
            if lamp.name not in dataDictionary['lights']:
                dataDictionary['lights'][lamp.name] = collections.OrderedDict()
            lightType = "directional"
            dataDictionary['lights'][lamp.name]['type'] = lightType
            viewDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
            dataDictionary['lights'][lamp.name]['direction'] = [viewDirection.x, viewDirection.z, viewDirection.y]
            dataDictionary['lights'][lamp.name]['radiance'] = [lamp.color.r, lamp.color.g, lamp.color.b]
            dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
        elif lamp.type == "SPOT":
            if lamp.name not in dataDictionary['lights']:
                dataDictionary['lights'][lamp.name] = collections.OrderedDict()
            lightType = "spot"
            dataDictionary['lights'][lamp.name]['type'] = lightType
            dataDictionary['lights'][lamp.name]['position'] = [lampObject.location.x, lampObject.location.z, lampObject.location.y]
            lightDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
            dataDictionary['lights'][lamp.name]['direction'] = [lightDirection.x, lightDirection.z, lightDirection.y]
            dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
            dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
            dataDictionary['lights'][lamp.name]['exponent'] = 4.0
            dataDictionary['lights'][lamp.name]['width'] = lamp.spot_size / 2
            dataDictionary['lights'][lamp.name]['falloffStart'] = lamp.spot_size / 2
        else:
            self.report({'WARNING'}, ("Skipping unsupported lamp type: \"%s\" from: \"%s\"." % (lamp.type, lamp.name)))
            continue
    world = scn.world
    worldTextureSlot = world.texture_slots[world.active_texture_index]
    if worldTextureSlot.texture is not None:
        if worldTextureSlot.texture.type != "IMAGE":
            self.report({'WARNING'}, ("Skipping environment map: \"%s\" because it is not an image." % worldTextureSlot.texture.name))
        else:
            if worldTextureSlot.texture.image is None:
                self.report({'WARNING'}, ("Skipping environment map: \"%s\" because it has no image." % worldTextureSlot.texture.name))
            else:
                dataDictionary['lights'][worldTextureSlot.texture.name] = collections.OrderedDict()
                absPath = bpy.path.abspath(worldTextureSlot.texture.image.filepath)
                finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                finalPath = finalPath.replace("\\", "/")
                lightType = "envmap"
                dataDictionary['lights'][worldTextureSlot.texture.name]["type"] = lightType
                dataDictionary['lights'][worldTextureSlot.texture.name]["map"] = finalPath
                dataDictionary['lights'][worldTextureSlot.texture.name]["scale"] = worldTextureSlot.horizon_factor

    # Materials

    materials = bpy.data.materials
    for i in range(len(materials)):
        material = materials[i]
        # Check for Multi-Layer Material
        layerCount = 0
        materialDepth = 0
        workDictionary = {}
        ignoreDiffuse = True
        ignoreSpecular = True
        unsupportedDiffuse = False
        addedLayer = False
        useWalter = False
        textureSlots = material.texture_slots
        textureMap = {}
        for j in range(len(textureSlots)):
            if not material.use_textures[j]:
                continue
            textureSlot = textureSlots[j]
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
                    self.report({'WARNING'}, ("Too many diffuse textures: \"%s\",\"%s\" from material: \"%s\"." % (textureSlots[textureMap['diffuse']].name, textureSlot.name, material.name)))
                else:
                    textureMap['diffuse'] = j
            if textureSlot.use_map_color_spec:
                if 'specular' in textureMap:
                    self.report({'WARNING'}, ("Too many specular textures: \"%s\",\"%s\" from material: \"%s\"." % (textureSlots[textureMap['specular']].name, textureSlot.name, material.name)))
                else:
                    textureMap['specular'] = j
            if textureSlot.use_map_emit:
                if 'emissive' in textureMap:
                    self.report({'WARNING'}, ("Too many emissive textures: \"%s\",\"%s\" from material: \"%s\"." % (textureSlots[textureMap['emissive']].name, textureSlot.name, material.name)))
                else:
                    textureMap['emissive'] = j
            if textureSlot.use_map_hardness:
                if 'roughness' in textureMap:
                    self.report({'WARNING'}, ("Too many roughness textures: \"%s\",\"%s\" from material: \"%s\"." % (textureSlots[textureMap['roughness']].name, textureSlot.name, material.name)))
                else:
                    textureMap['roughness'] = j
        if material.use_transparency:
            useWalter = True
        if material.diffuse_shader == "LAMBERT" or material.diffuse_shader == "OREN_NAYAR" or material.diffuse_shader == "FRESNEL" or useWalter:
            if material.diffuse_intensity != 0 or 'diffuse' in textureMap:  # ignore if factor == 0 and no Texture
                if material.diffuse_shader == "FRESNEL":
                    ignoreSpecular = False
                layerCount += 1
                ignoreDiffuse = False
        elif 'diffuse' in textureMap:
            self.report({'WARNING'}, ("Initialised diffuse Texture: \"%s\" from unsupported diffuse material: \"%s\" as Lambert material." % (textureSlots[textureMap['diffuse']].name, material.name)))
            layerCount += 1
            ignoreDiffuse = False
            unsupportedDiffuse = True
        if material.specular_shader == "COOKTORR":
            if material.specular_intensity != 0 or 'specular' in textureMap:  # ignore if factor == 0 and no Texture
                if not material.diffuse_shader == "FRESNEL" or ignoreDiffuse:  # if Fresnel dont add a blend layer
                    layerCount += 1
                ignoreSpecular = False
        elif material.diffuse_shader != "FRESNEL":
            if 'specular' in textureMap:
                self.report({'WARNING'}, ("Ignored specular Texture: \"%s\" from unsupported specular material: \"%s\"." % (textureSlots[textureMap['specular']].name, material.name)))
            if 'roughness' in textureMap:
                self.report({'WARNING'}, ("Ignored roughness Texture: \"%s\" from unsupported specular material: \"%s\"." % (textureSlots[textureMap['roughness']].name, material.name)))
        if material.emit != 0:
            layerCount += 1
        if material.name not in dataDictionary['materials']:
            dataDictionary['materials'][material.name] = collections.OrderedDict()
        if layerCount == 1:
            workDictionary = dataDictionary['materials'][material.name]
        elif layerCount > 1:
            materialDepth = 1
            materialType = "blend"
            dataDictionary['materials'][material.name]['type'] = materialType
            if 'layerA' not in dataDictionary['materials'][material.name]:
                dataDictionary['materials'][material.name]['layerA'] = collections.OrderedDict()
            if 'layerB' not in dataDictionary['materials'][material.name]:
                dataDictionary['materials'][material.name]['layerB'] = collections.OrderedDict()
            workDictionary = dataDictionary['materials'][material.name]['layerA']
        else:
            self.report({'WARNING'}, ("Initialised unsupported material: \"%s\" as lambert material." % material.name))
            materialType = "lambert"
            dataDictionary['materials'][material.name]['type'] = materialType
            dataDictionary['materials'][material.name]['albedo'] = [material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b]
            continue
        currentLayer = 0
        if material.emit == 0 and 'emissive' in textureMap:
            self.report({'WARNING'}, ("Ignored emissive texture: \"%s\" from material:\"%s\" because emit factor is 0." % (textureSlots[textureMap['emissive']].name, material.name)))
        if material.emit != 0:
            materialType = "emissive"
            workDictionary['type'] = materialType
            if 'emissive' in textureMap:
                absPath = bpy.path.abspath(textureSlots[textureMap['emissive']].texture.image.filepath)
                finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                finalPath = finalPath.replace("\\", "/")
                workDictionary['radiance'] = finalPath
            else:
                workDictionary['radiance'] = [material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b]
            workDictionary['scale'] = [material.emit, material.emit, material.emit]
            currentLayer += 1
            if layerCount != 1:  # if blend Material
                workDictionary = dataDictionary['materials'][material.name]
                workDictionary['factorA'] = 1.0
                workDictionary['factorB'] = 1.0  # for emissive both factors = 1
                workDictionary = workDictionary['layerB']
                if layerCount - currentLayer > 1:  # if we need an additional layer pair
                    materialDepth += 1
                    materialType = "blend"
                    workDictionary['type'] = materialType
                    if 'layerA' not in workDictionary:
                        workDictionary['layerA'] = collections.OrderedDict()
                    if 'layerB' not in workDictionary:
                        workDictionary['layerB'] = collections.OrderedDict()
                    workDictionary = workDictionary['layerA']
        if not ignoreDiffuse:
            if useWalter and material.diffuse_shader != "FRESNEL":  # if Fresnel do a fresnel material otherwise walter
                materialType = "walter"
                workDictionary['type'] = materialType
                if 'roughness' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['roughness']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['roughness'] = finalPath
                else:
                    workDictionary['roughness'] = (1 - material.specular_alpha)
                if "ndf" in material:
                    workDictionary['ndf'] = material["ndf"]
                else:
                    workDictionary['ndf'] = "GGX"  # Default normal distribution function
                absorptionFactor = material.alpha
                workDictionary['absorption'] = [material.diffuse_color.r * absorptionFactor, material.diffuse_color.g * absorptionFactor, material.diffuse_color.b * absorptionFactor]
                currentLayer += 1
                addedLayer = True
            elif material.diffuse_shader == "LAMBERT" or unsupportedDiffuse:
                materialType = "lambert"
                workDictionary['type'] = materialType
                if 'diffuse' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['diffuse']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['albedo'] = finalPath
                else:
                    workDictionary['albedo'] = [material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b]
                currentLayer += 1
                addedLayer = True
            elif material.diffuse_shader == "OREN_NAYAR":
                materialType = "orennayar"
                workDictionary['type'] = materialType
                if 'diffuse' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['diffuse']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['albedo'] = finalPath
                else:
                    workDictionary['albedo'] = [material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b]
                workDictionary['roughness'] = material.roughness # blender is from 0 to pi and in the specifications to pi/2 but pi is ok
                currentLayer += 1
                addedLayer = True
            elif material.diffuse_shader == "FRESNEL":
                materialType = "fresnel"
                workDictionary['type'] = materialType
                workDictionary['refractionIndex'] = material.diffuse_fresnel
                if 'layerRefraction' not in workDictionary:
                    workDictionary['layerRefraction'] = collections.OrderedDict()
                if useWalter:
                    materialType = "walter"
                    workDictionary['layerRefraction']['type'] = materialType
                    if 'roughness' in textureMap:
                        absPath = bpy.path.abspath(textureSlots[textureMap['roughness']].texture.image.filepath)
                        finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                        finalPath = finalPath.replace("\\", "/")
                        workDictionary['layerRefraction']['roughness'] = finalPath
                    else:
                        workDictionary['layerRefraction']['roughness'] = (1 - material.specular_alpha)
                    if "ndf" in material:
                        workDictionary['ndf'] = material["ndf"]
                    else:
                        workDictionary['ndf'] = "GGX"  # Default normal distribution function
                    absorptionFactor = material.alpha
                    workDictionary['layerRefraction']['absorption'] = [material.diffuse_color.r*absorptionFactor, material.diffuse_color.g*absorptionFactor, material.diffuse_color.b*absorptionFactor]
                else:
                    materialType = "lambert"
                    workDictionary['layerRefraction']['type'] = materialType
                    if 'diffuse' in textureMap:
                        absPath = bpy.path.abspath(textureSlots[textureMap['diffuse']].texture.image.filepath)
                        finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                        finalPath = finalPath.replace("\\", "/")
                        workDictionary['layerRefraction']['albedo'] = finalPath
                    else:
                        workDictionary['layerRefraction']['albedo'] = [material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b]
                if 'layerReflection' not in workDictionary:
                    workDictionary['layerReflection'] = collections.OrderedDict()
                workDictionary = workDictionary['layerReflection']
                # No currentLayer += 1  and addedLayer = True here because the layer is not finished yet
            if layerCount != 1:  # if blend Material
                if addedLayer:
                    addedLayer = False
                    if layerCount - currentLayer == 0:  # check if current layer was A or B
                        factorName = "factorB"
                    else:
                        factorName = "factorA"
                    workDictionary = dataDictionary['materials'][material.name]
                    k = 0
                    while materialDepth - k > 1:  # go to the root dictionary of the work dictionary
                        workDictionary = workDictionary['layerB']
                        k += 1
                    workDictionary[factorName] = material.diffuse_intensity
                    if layerCount - currentLayer != 0:  # if current layer was A set work dictionary to B
                        workDictionary = workDictionary['layerB']
        if not ignoreSpecular:
            if material.specular_shader == "COOKTORR":
                materialType = "torrance"
                workDictionary['type'] = materialType
                if 'specular' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['specular']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['albedo'] = finalPath
                else:
                    workDictionary['albedo'] = [material.specular_color.r, material.specular_color.g, material.specular_color.b]
                if 'roughness' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['roughness']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['roughness'] = finalPath
                else:
                    workDictionary['roughness'] = material.specular_hardness / 511  # Max hardness = 511
                if "ndf" in material:
                    workDictionary['ndf'] = material["ndf"]
                else:
                    workDictionary['ndf'] = "GGX"  # Default normal distribution function
                currentLayer += 1
                addedLayer = True
            else:  # Else we have Fresnel and we have to use a default torrance
                materialType = "torrance"
                workDictionary['type'] = materialType
                if 'specular' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['specular']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['albedo'] = finalPath
                else:
                    workDictionary['albedo'] = [material.specular_color.r, material.specular_color.g, material.specular_color.b]
                if 'roughness' in textureMap:
                    absPath = bpy.path.abspath(textureSlots[textureMap['roughness']].texture.image.filepath)
                    finalPath = os.path.relpath(absPath, os.path.dirname(filepath))
                    finalPath = finalPath.replace("\\", "/")
                    workDictionary['roughness'] = finalPath
                else:
                    workDictionary['roughness'] = 0  # We could have no roughness parameter so we default to 0
                if "ndf" in material:
                    workDictionary['ndf'] = material["ndf"]
                else:
                    workDictionary['ndf'] = "GGX"  # Default normal distribution function
                currentLayer += 1
                addedLayer = True
            if layerCount != 1:  # if blend Material
                if addedLayer:
                    if layerCount - currentLayer == 0:  # check if current layer was A or B
                        factorName = "factorB"
                    else:
                        factorName = "factorA"
                    workDictionary = dataDictionary['materials'][material.name]
                    k = 0
                    while materialDepth - k > 1:  # go to the root dictionary of the work dictionary
                        workDictionary = workDictionary['layerB']
                        k += 1
                    workDictionary[factorName] = material.specular_intensity
                    if layerCount - currentLayer != 0:  # if current layer was A set work dictionary to B
                        workDictionary = workDictionary['layerB']
        if currentLayer != layerCount:
            self.report({'ERROR'}, ("Expected %d layers but %d were used at material: \"%s\"." % (layerCount, currentLayer, material.name)))
            return -1

    # Scenarios
    if len(bpy.data.scenes) > 1:
        self.report({'WARNING'}, ("Multiple scenes found. Only exporting active scene."))
    if scn.name not in dataDictionary['scenarios']:
        dataDictionary['scenarios'][scn.name] = collections.OrderedDict()
    cameraName = ''  # type: str
    if 'name' in scn.camera:
        if scn.camera.data.type == "PERSP" or scn.camera.data.type == "ORTHO":
            cameraName = scn.camera.name
        else:
            cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary
            # dataDictionary['cameras'] has at least 1 element otherwise the program would have exited earlier
    else:
        cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary

    dataDictionary['scenarios'][scn.name]['camera'] = cameraName
    dataDictionary['scenarios'][scn.name]['resolution'] = [scn.render.resolution_x, scn.render.resolution_y]
    lamps = [o for o in bpy.data.objects if o.type == 'LAMP']
    lights = []
    for i in range(len(lamps)):
        lamp = lamps[i].data
        if lamp.type == "POINT" or lamp.type == "SUN" or lamp.type == "SPOT":
            lights.append(lamp.name)

    dataDictionary['scenarios'][scn.name]['lights'] = lights
    dataDictionary['scenarios'][scn.name]['lod'] = 0
    # Material assignments
    if 'materialAssignments' not in dataDictionary['scenarios'][scn.name]:
        dataDictionary['scenarios'][scn.name]['materialAssignments'] = collections.OrderedDict()
    for material in materials:
        dataDictionary['scenarios'][scn.name]['materialAssignments'][material.name] = material.name

    # Object properties
    if 'objectProperties' not in dataDictionary['scenarios'][scn.name]:
        dataDictionary['scenarios'][scn.name]['objectProperties'] = collections.OrderedDict()
    # TODO objectProperties

    # To reduce float precision it is necessary to do the store->load->store (even if it is ugly)
    dump = json.dumps(json.loads(json.dumps(dataDictionary, indent=4), object_pairs_hook=OrderedDict, parse_float=lambda x: round(float(x), 3)), indent=4)

    vectorOccurrences = re.findall(r"[[](?:\s*-?\d+(?:\.\d+)?,){0,3}\s*-?\d+(?:\.\d+)?,?\s*[\]]", dump)  # Find Vec1-4 with regular expression
    for vec in vectorOccurrences:
        print(vec)
        shortVector3 = re.sub(r"\s+", " ", vec)  # Shorten it
        print(shortVector3)
        dump = dump.replace(vec, shortVector3)
    file = open(filepath, 'w')
    file.write(dump)
    file.close()
    return 0

def export_binary(context, self, filepath, use_selection, use_deflation, use_compression):
    scn = context.scene
    # Binary
    binary = bytearray()
    # Materials Header
    binary.extend("Mats".encode())
    materials = bpy.data.materials
    materialNames = []
    materialNameLengths = []
    materialLookup = collections.OrderedDict()  # Lookup for the polygons
    bytePosition = 16
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
    objects = bpy.data.objects

    for i in range(len(objects)):
        if objects[i].type != "MESH":
            continue
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
            if len(currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them)
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
        boundingBox = currentObject.bound_box
        # Set bounding box min/max to last element of the bb
        boundingBoxMin = [boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]]
        boundingBoxMax = [boundingBox[7][0], boundingBox[7][1], boundingBox[7][2]]
        for j in range(7):  # 8-1
            for k in range(3):  # x z y
                if boundingBox[j][k] < boundingBoxMin[k]:
                    boundingBoxMin[k] = boundingBox[j][k]
                if boundingBox[j][k] > boundingBoxMax[k]:
                    boundingBoxMax[k] = boundingBox[j][k]
        binary.extend(struct.pack('<f', boundingBoxMin[0]))  # 0 2 1 is correct right handed -> left handed
        binary.extend(struct.pack('<f', boundingBoxMin[2]))  # '<' = little endian 'f' = float
        binary.extend(struct.pack('<f', boundingBoxMin[1]))
        binary.extend(struct.pack('<f', boundingBoxMax[0]))
        binary.extend(struct.pack('<f', boundingBoxMax[2]))
        binary.extend(struct.pack('<f', boundingBoxMax[1]))
        # <Jump Table>
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
                    self.report({'WARNING'}, ("Skipped LOD levels from: \"%s\" because LOD Object: \"%s\" has no successor." % (currentObject.name, lodObject.name)))
                    break
                if maxDistance < lodObject.lod_levels[1].distance:
                    maxDistance = lodObject.lod_levels[1].distance  # the last LOD level has the highest distance
                    lodChainStart = len(lodLevels)-1
                lodObject = lodObject.lod_levels[1].object
        if len(lodLevels) == 0:
            lodLevels.append(currentObject)  # if no LOD levels the object itself ist the only LOD level
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
            if len(lodObject.data.uv_layers):
                # mark seams from uv islands
                bpy.ops.uv.seams_from_islands()
                seams = [e for e in bm.edges if e.seam]
                # split on seams
                bmesh.ops.split_edges(bm, edges=seams)

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
            numberOfVertexAttributes = 0  # TODO Vertex Attributes
            numberOfFaceAttributes = 0  # TODO Face Attributes
            numberOfSphereAttributes = 0
            if "sphere" in lodObject: # Change Values if it is an sphere
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
            binary.extend(numberOfVertexAttributes.to_bytes(4, byteorder='little'))
            # Number of Face Attributes
            binary.extend(numberOfFaceAttributes.to_bytes(4, byteorder='little'))
            # Number of Sphere Attributes
            binary.extend(numberOfSphereAttributes.to_bytes(4, byteorder='little'))
            if "sphere" not in lodObject:
                # Vertex data
                vertices = mesh.vertices
                mesh.calc_normals()
                uvCoordinates = numpy.empty(len(vertices), dtype=object)
                if len(mesh.uv_layers) == 0:
                    self.report({'WARNING'}, ("LOD Object: \"%s\" has no uv layers." % (lodObject.name)))
                    for k in range(len(uvCoordinates)):
                        acos = math.acos(vertices[k].co[1]/(math.sqrt((vertices[k].co[0]*vertices[k].co[0]) + (vertices[k].co[1]*vertices[k].co[1]) + (vertices[k].co[2]*vertices[k].co[2]))))
                        arctan2 = numpy.arctan2(vertices[k].co[2], vertices[k].co[0])
                        uvCoordinates[k] = [acos, arctan2]
                else:
                    uv_layer = mesh.uv_layers.active.data
                    for polygon in mesh.polygons:
                        for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                            uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer[loop_index].uv
                vertexDataArray = bytearray()  # Used for deflation
                for k in range(len(vertices)):
                    vertexDataArray.extend(struct.pack('<f', mesh.vertices[k].co[0]))
                    vertexDataArray.extend(struct.pack('<f', mesh.vertices[k].co[2]))
                    vertexDataArray.extend(struct.pack('<f', mesh.vertices[k].co[1]))
                for k in range(len(vertices)):
                    if use_compression:
                        vertexDataArray.extend(pack_normal32(mesh.vertices[k].normal).to_bytes(4, byteorder='little'))
                    else:
                        vertexDataArray.extend(struct.pack('<f', mesh.vertices[k].normal[0]))
                        vertexDataArray.extend(struct.pack('<f', mesh.vertices[k].normal[2]))
                        vertexDataArray.extend(struct.pack('<f', mesh.vertices[k].normal[1]))
                for k in range(len(vertices)):
                    vertexDataArray.extend(struct.pack('<f', uvCoordinates[k][0]))
                    vertexDataArray.extend(struct.pack('<f', uvCoordinates[k][1]))
                vertexOutData = vertexDataArray
                if use_deflation and len(vertexDataArray) > 0:
                    vertexOutData = zlib.compress(vertexDataArray, 8)  # TODO deflate algorithm doesn't do what we want it to do
                    binary.extend(len(vertexOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(vertexDataArray).to_bytes(4, byteorder='little'))
                binary.extend(vertexOutData)

                # Attributes
                # TODO Attributes (with deflation)
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
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 3:
                        matIDDataArray.extend(materialLookup[mesh.materials[polygon.material_index].name].to_bytes(2, byteorder='little'))
                for polygon in mesh.polygons:
                    if len(polygon.vertices) == 4:
                        matIDDataArray.extend(materialLookup[mesh.materials[polygon.material_index].name].to_bytes(2, byteorder='little'))
                if (objectFlags & 1) == 0:
                    for polygon in mesh.polygons:
                        if mesh.materials[polygon.material_index].emit > 0.0:
                            objectFlags |= 1
                            objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
                            for k in range(4):
                                binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]
                            break
                matIDOutData = matIDDataArray
                if use_deflation and len(matIDDataArray) > 0:
                    matIDOutData = zlib.compress(matIDDataArray, 8)
                    binary.extend(len(matIDOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(matIDDataArray).to_bytes(4, byteorder='little'))
                binary.extend(matIDOutData)
                # Face Attributes
                # TODO Face Attributes (with deflation)
            else:
                # Spheres
                sphereDataArray = bytearray()
                center = []
                center.append((boundingBoxMin[0] + boundingBoxMax[0])/2)
                center.append((boundingBoxMin[1] + boundingBoxMax[1]) / 2)
                center.append((boundingBoxMin[2] + boundingBoxMax[2]) / 2)
                radius = abs(boundingBoxMin[0]-(center[0]))
                sphereDataArray.extend(struct.pack('<f', center[0]))
                sphereDataArray.extend(struct.pack('<f', center[2]))
                sphereDataArray.extend(struct.pack('<f', center[1]))
                sphereDataArray.extend(struct.pack('<f', radius))

                sphereDataArray.extend(materialLookup[lodObject.active_material.name].to_bytes(2, byteorder='little'))

                if (objectFlags & 1) == 0:
                    if lodObject.active_material.emit > 0.0:
                        objectFlags |= 1
                        objectFlagsBin = objectFlags.to_bytes(4, byteorder='little')
                        for k in range(4):
                            binary[objectFlagsBinaryPosition + k] = objectFlagsBin[k]

                sphereOutData = sphereDataArray
                # TODO Sphere Attributes
                if use_deflation and len(sphereDataArray) > 0:
                    matIDOutData = zlib.compress(sphereDataArray, 8)
                    binary.extend(len(sphereOutData).to_bytes(4, byteorder='little'))
                    binary.extend(len(sphereDataArray).to_bytes(4, byteorder='little'))
                binary.extend(sphereOutData)
            bpy.data.meshes.remove(mesh)
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
        if currentObject.type != "MESH":
            continue
        if len(currentObject.lod_levels) != 0:  # if object has LOD levels
            if len(currentObject.data.vertices) != 0:  # if it has data ( objects with LOD have no data, but the LODs are objects too and have data skip them)
                continue
        if currentObject.data in usedMeshes:
            index = usedMeshes.index(currentObject.data)
            binary.extend(index.to_bytes(4, byteorder='little'))  # Object ID
            binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Keyframe
            binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little'))  # TODO Instance ID
            transformMat = currentObject.matrix_world
            for k in range(4):
                binary.extend(struct.pack('<f', transformMat[0][k]))
            for k in range(4):
                binary.extend(struct.pack('<f', transformMat[2][k]))
            for k in range(4):
                binary.extend(struct.pack('<f', transformMat[1][k]))
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
                   use_deflation):
    filename = os.path.splitext(filepath)[0]
    binfilepath = filename + ".mff"
    if export_json(context, self, filepath, binfilepath) == 0:
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
    l1norm = abs(vec3[0]) + abs(vec3[2]) + abs(vec3[1])
    if vec3[1] >= 0:
        u = vec3[0] / l1norm
        v = vec3[2] / l1norm
    else:  # warp lower hemisphere
        u = (1 - abs(vec3[2]) / l1norm) * (1 if vec3[0] >= 0 else -1)
        v = (1 - abs(vec3[0]) / l1norm) * (1 if vec3[2] >= 0 else -1)
    u = math.floor((u / 2 + 0.5) * 65535.49 + 0.5)  # from [-1,1] to [0,2^16-1]
    v = math.floor((v / 2 + 0.5) * 65535.49 + 0.5)  # from [-1,1] to [0,2^16-1]
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
    path_mode = path_reference_mode

    def execute(self, context):
        return export_mufflon(context, self, self.filepath, self.use_selection,
                              self.use_compression, self.use_deflation)


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
