import bpy
from mathutils import Vector
import os
import json
import collections
import struct

# TODO Load old json (as dictionary) and override ONLY the existing data

version = "0.1"
binary = "Path/To/Binary.bin"

scn = bpy.context.scene

dataDictionary = collections.OrderedDict()
dataDictionary['version'] = version
dataDictionary['binary'] = binary
dataDictionary['cameras'] = collections.OrderedDict()
dataDictionary['lights'] = collections.OrderedDict()
dataDictionary['materials'] = collections.OrderedDict()
dataDictionary['scenarios'] = collections.OrderedDict()

# Cameras

cameras = bpy.data.cameras

for i in range(len(cameras)):
    camera = cameras[i]
    cameraObject = bpy.data.objects[camera.name]
    if camera.type == "PERSP":
        aperture = camera.gpu_dof.fstop
        if aperture == 128.0:
            cameraType = "pinhole"
            dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            dataDictionary['cameras'][camera.name]['type'] = cameraType
            fov = camera.angle * 180 / 3.141592653589793  # convert rad to degree
            dataDictionary['cameras'][camera.name]['fov'] = fov
        else:
            cameraType = "focus"
            dataDictionary['cameras'][camera.name] = collections.OrderedDict()
            dataDictionary['cameras'][camera.name]['type'] = cameraType
            dataDictionary['cameras'][camera.name]['focalLength'] = camera.lens
            dataDictionary['cameras'][camera.name]['chipHeight'] = camera.sensor_height
            dataDictionary['cameras'][camera.name]['focusDistance'] = camera.dof_distance
            dataDictionary['cameras'][camera.name]['aperture'] = aperture
    elif camera.type == "ORTHO":
        cameraType = "ortho"
        dataDictionary['cameras'][camera.name] = collections.OrderedDict()
        dataDictionary['cameras'][camera.name]['type'] = cameraType
        orthoWidth = camera.ortho_scale
        dataDictionary['cameras'][camera.name]['width'] = orthoWidth
        orthoHeight = scn.render.resolution_y / scn.render.resolution_x * orthoWidth  # get aspect ratio via resolution
        dataDictionary['cameras'][camera.name]['height'] = orthoHeight
    else:
        print("Skipping unsupported camera type: \"%s\" from: \"%s\"" % camera.type, camera.name)
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

# Lights

lamps = bpy.data.lamps
for i in range(len(lamps)):
    lamp = lamps[i]
    lampObject = bpy.data.objects[lamp.name]
    if lamp.type == "POINT":
        lightType = "point"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        dataDictionary['lights'][lamp.name]['position'] = [lampObject.location.x, lampObject.location.z, lampObject.location.y]
        dataDictionary['lights'][lamp.name]['intensity'] = [lamp.color.r, lamp.color.g, lamp.color.b]
        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
    elif lamp.type == "SUN":
        lightType = "directional"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
        dataDictionary['lights'][lamp.name]['type'] = lightType
        viewDirection = lampObject.matrix_world.to_quaternion() * Vector((0.0, 0.0, -1.0))
        dataDictionary['lights'][lamp.name]['direction'] = [viewDirection.x, viewDirection.z, viewDirection.y]
        dataDictionary['lights'][lamp.name]['radiance'] = [lamp.color.r, lamp.color.g, lamp.color.b]
        dataDictionary['lights'][lamp.name]['scale'] = lamp.energy
    elif lamp.type == "SPOT":
        lightType = "spot"
        dataDictionary['lights'][lamp.name] = collections.OrderedDict()
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
        print("Skipping unsupported lamp type: \"%s\" from: \"%s\"" % lamp.type, lamp.name)
        continue
    # TODO envmap, goniometric

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
    if material.diffuse_shader == "LAMBERT" or material.diffuse_shader == "OREN_NAYAR" or material.diffuse_shader == "FRESNEL":
        if material.diffuse_intensity != 0:  # ignore if factor == 0
            layerCount += 1
            ignoreDiffuse = False
    if material.specular_shader == "COOKTORR":
        if material.specular_intensity != 0:  # ignore if factor == 0
            layerCount += 1
            ignoreSpecular = False
    if material.emit != 0:
        layerCount += 1
    if layerCount == 1:
        dataDictionary['materials'][material.name] = collections.OrderedDict()
        workDictionary = dataDictionary['materials'][material.name]
    elif layerCount > 1:
        materialDepth = 1
        materialType = "blend"
        dataDictionary['materials'][material.name] = collections.OrderedDict()
        dataDictionary['materials'][material.name]['type'] = materialType
        dataDictionary['materials'][material.name]['layerA'] = collections.OrderedDict()
        dataDictionary['materials'][material.name]['layerB'] = collections.OrderedDict()
        workDictionary = dataDictionary['materials'][material.name]['layerA']
    else:
        print("Skipping unsupported material:\"%s\"" % material.name)
        continue
    currentLayer = 0
    if material.emit != 0:
        materialType = "emissive"
        workDictionary['type'] = materialType
        workDictionary['radiance'] = ([material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b])
        workDictionary['scale'] = material.emit
        currentLayer += 1
        if layerCount != 1:  # if blend Material
            workDictionary = dataDictionary['materials'][material.name]
            workDictionary['factorA'] = 1.0
            workDictionary['factorB'] = 1.0  # for emissive both factors = 1
            workDictionary = workDictionary['layerB']
            if layerCount - currentLayer > 1:  # if we need an additional layer pair
                materialDepth += 1
                materialType = "blend"
                workDictionary['layerA'] = collections.OrderedDict()
                workDictionary['layerB'] = collections.OrderedDict()
                workDictionary = workDictionary['layerA']
    if not ignoreDiffuse:
        if material.diffuse_shader == "LAMBERT":
            materialType = "lambert"
            workDictionary['type'] = materialType
            workDictionary['albedo'] = ([material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b])
            currentLayer += 1
            addedLayer = True
        elif material.diffuse_shader == "OREN_NAYAR":
            materialType = "orennayar"
            workDictionary['type'] = materialType
            workDictionary['albedo'] = ([material.diffuse_color.r, material.diffuse_color.g, material.diffuse_color.b])
            workDictionary['roughness'] = material.roughness
            currentLayer += 1
            addedLayer = True
        elif material.diffuse_shader == "FRESNEL":
            materialType = "fresnel"
            workDictionary['type'] = materialType
            # TODO Finish Fresnel
            currentLayer += 1
            addedLayer = True
        if layerCount != 1:  # if blend Material
            if addedLayer:
                addedLayer = False
                factorName = ""
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
            workDictionary['albedo'] = ([material.specular_color.r, material.specular_color.g, material.specular_color.b])
            workDictionary['roughness'] = material.specular_hardness / 511 # Max hardness = 511
            currentLayer += 1
            addedLayer = True
        if layerCount != 1:  # if blend Material
            if addedLayer:
                addedLayer = False
                factorName = ""
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
        print("Error: Expected %d layers but %d were used at material: \"%s\"" % (layerCount,currentLayer ,material.name))
    # TODO Other Materials (walter (roughness = 1-material.raytrace_transparency_gloss_factor ))

# Scenarios

dataDictionary['scenarios'][scn.name] = collections.OrderedDict()
dataDictionary['scenarios'][scn.name]['camera'] = scn.camera.name  # TODO default if no camera (Error message)
dataDictionary['scenarios'][scn.name]['resolution'] = [scn.render.resolution_x, scn.render.resolution_y]
lamps = bpy.data.lamps
lights = []
for i in range(len(lamps)):
    lamp = lamps[i]
    if lamp.type == "POINT" or lamp.type == "SUN" or lamp.type == "SPOT":
        lights.append(lamp.name)
dataDictionary['scenarios'][scn.name]['lights'] = lights  # TODO default if no light (No Light)
dataDictionary['scenarios'][scn.name]['lod'] = 0
# TODO Finish Scenarios (need binary for this)

print(json.dumps(dataDictionary, indent=4))

file = open(os.path.splitext(bpy.data.filepath)[0] + ".json", 'w')
file.write(json.dumps(dataDictionary, indent=4))
file.close()








# Binary
binary = bytearray()
# Materials Header
binary.extend("Mats".encode())
materials = bpy.data.materials
countOfMaterials = 0
materialNames = []
materialNameLengths = []
bytePosition = 16
for i in range(len(materials)):
    material = materials[i]
    if material.diffuse_shader == "LAMBERT" or material.diffuse_shader == "OREN_NAYAR" or material.diffuse_shader == "FRESNEL":
        if material.diffuse_intensity != 0:  # ignore if factor == 0
            layerCount += 1
    if material.specular_shader == "COOKTORR":
        if material.specular_intensity != 0:  # ignore if factor == 0
            layerCount += 1
    if material.emit != 0:
        layerCount += 1
    if layerCount == 0:
        continue
    countOfMaterials += 1
    materialNames.append(material.name.encode())
    materialNameLength = len(material.name.encode())
    materialNameLengths.append(materialNameLength.to_bytes(4, byteorder='little'))
    bytePosition += 4 + materialNameLength

binary.extend(bytePosition.to_bytes(8, byteorder='little'))
binary.extend(countOfMaterials.to_bytes(4, byteorder='little'))
for i in range(len(materialNameLengths)):
    binary.extend(materialNameLengths[i])
    binary.extend(materialNames[i])

# Objects Header

# Type
binary.extend("Objs".encode())
nextSectionStartBinaryPosition = len(binary)  # Save Position in binary has to be corrected later
# Next section start position
binary.extend((0).to_bytes(8, byteorder='little'))  # has to be corrected when the value is known

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

flags = 0
binary.extend(flags.to_bytes(4, byteorder='little'))  # has to be corrected when the value is known

currentObjectNumber = 0
meshes = bpy.data.meshes
usedMeshes = []
for i in range(len(objects)):
    currentObject = objects[i]
    if currentObject.type != "MESH":
        continue
    if currentObject.data in usedMeshes:
        continue
    if len(currentObject.lod_levels) != 0:  # if object has LOD levels
        if len(currentObject.data.edges) != 0:  # if it has geometry ( objects with LOD levels have no geometry)
            continue
    usedMeshes.append(currentObject.data)
    objectStartPosition = len(binary).to_bytes(4, byteorder='little')
    for j in range(4):
        binary[objectStartBinaryPosition[currentObjectNumber]+j] = objectStartPosition[j]
    currentObjectNumber += 1
    # Type check
    binary.extend("Obj_".encode())
    # Object name
    objectName = currentObject.data.name.encode()
    objectNameLength = len(objectName)
    binary.extend(objectNameLength.to_bytes(4, byteorder='little'))
    binary.extend(objectName)
    # keyframe
    binary.extend((0xFFFFFFFF).to_bytes(4, byteorder='little')) # TODO keyframes
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
        lastLodChainObject = lodObject
        while lodObject not in lodLevels:
            lodLevels.append(lodObject)
            if len(lodObject.lod_levels) == 0:
                lodLevels = []
                lodChainStart = 0
                print("Warning: Skipped LOD levels from: \"%s\" because LOD Object: \"%s\" has no successor" % currentObject.name, lodObject.name)
                break
            if maxDistance < lodObject.lod_levels[1].distance:
                maxDistance = lodObject.lod_levels[1].distance  # the last LOD level has the highest distance
                lastLodChainObject = lodObject  # it is not lodObject.lod_levels[1].object because of the specification
                lodChainStart = len(lodLevels)-1
            lodObject = lodObject.lod_levels[1].object
    if len(lodLevels) == 0:
        lodLevels.append(currentObject)  # if no LOD levels the object itself ist the only LOD level
    # Number of entries in table
    binary.extend((len(lodLevels)).to_bytes(4, byteorder='little'))
    lodStartBinaryPosition = []
    for j in range(len(lodLevels)):
        lodObject = lodLevels[(lodChainStart+j) % len(lodLevels)]  # for the correct starting position
        # start Positions
        lodStartBinaryPosition.append(len(binary))
        binary.extend((0).to_bytes(8, byteorder='little'))  # TODO has to be corrected when the value is known
        # Type
        binary.extend("LOD_".encode())
        mesh = lodObject.data

binFile = open(os.path.splitext(bpy.data.filepath)[0] + ".mff", 'bw')
binFile.write(binary)
binFile.close()

