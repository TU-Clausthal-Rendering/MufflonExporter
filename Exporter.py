import bpy
import bmesh
from mathutils import Vector
import os
import json
import collections
import struct
import datetime
import numpy
import math

# TODO Load old json (as dictionary) and override ONLY the existing data
# TODO Warning if multiple Scenes exist
# TODO Apply Subivision Modifyer before exporting
bl_info = {
        "name": "Mufflon Exporter",
		"description": "Exporter for the custom Mufflon file format",
		"author": "Marvin",
		"version": (0, 1),
		"blender": (2, 69, 0),
		"location": "File > Export > Mufflon (.json/.mff)",
		"category": "Import-Export"
}

def export_json(context, filepath, binfilepath):
    version = "1.0"
    binary = binfilepath

    scn = context.scene

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

    if len(dataDictionary['cameras']) == 0:
        print("Stopped exporting: No camera found")  # Stop if no camera was exported
        return -1

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
                    workDictionary['type'] = materialType
                    workDictionary['layerA'] = collections.OrderedDict()
                    workDictionary['layerB'] = collections.OrderedDict()
                    workDictionary = workDictionary['layerA']
        if not ignoreDiffuse:
            addedLayer = False
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
                workDictionary['roughness'] = material.specular_hardness / 511  # Max hardness = 511
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
            return -1
        # TODO Other Materials (walter (roughness = 1-material.raytrace_transparency_gloss_factor ))

    # Scenarios

    dataDictionary['scenarios'][scn.name] = collections.OrderedDict()
    cameraName = ''  # type: str
    if hasattr(scn.camera, 'name'):
        if scn.camera.type == "PERSP" or scn.camera.type == "ORTHO":
            cameraName = scn.camera.name
        else:
            cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary
            # dataDictionary['cameras'] has at least 1 element otherwise the program would have exited earlier
    else:
        cameraName = next(iter(dataDictionary['cameras']))  # gets first camera in dataDictionary

    dataDictionary['scenarios'][scn.name]['camera'] = cameraName
    dataDictionary['scenarios'][scn.name]['resolution'] = [scn.render.resolution_x, scn.render.resolution_y]
    lamps = bpy.data.lamps
    lights = []
    for i in range(len(lamps)):
        lamp = lamps[i]
        if lamp.type == "POINT" or lamp.type == "SUN" or lamp.type == "SPOT":
            lights.append(lamp.name)

    dataDictionary['scenarios'][scn.name]['lights'] = lights
    dataDictionary['scenarios'][scn.name]['lod'] = 0
    # TODO Finish Scenarios (need binary for this)

    print(json.dumps(dataDictionary, indent=4))

    file = open(filepath, 'w')
    file.write(json.dumps(dataDictionary, indent=4))
    file.close()
    return 0


def export_binary(context, filepath):
    scn = context.scene
    # Binary
    binary = bytearray()
    # Materials Header
    binary.extend("Mats".encode())
    materials = bpy.data.materials
    countOfMaterials = 0
    layerCount = 0
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
            if currentObject.data is None:  # if it has data ( objects with LOD levels have no data)
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
                    print("Warning: Skipped LOD levels from: \"%s\" because LOD Object: \"%s\" has no successor" % (currentObject.name, lodObject.name))
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
            mesh = lodObject.to_mesh(scn, True, calc_tessface=False, settings='RENDER')
            bm = bmesh.new()
            bm.from_mesh(mesh)  # bmesh gives a local editable mesh copy
            faces = bm.faces
            faces.ensure_lookup_table()
            facesToTriangulate = []
            for k in range(len(faces)):
                if len(faces[k].edges) > 4:
                    facesToTriangulate.append(faces[k])
                    print(k)
            bmesh.ops.triangulate(bm, faces=facesToTriangulate[:], quad_method=0, ngon_method=0)

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
                    print("Error: %d Vertices in face from object: %s" % (numVertices, lodObject.name))
                    return
            bm.to_mesh(mesh)
            bm.free()
            # Number of Triangles
            binary.extend(numberOfTriangles.to_bytes(4, byteorder='little'))
            # Number of Quads
            binary.extend(numberOfQuads.to_bytes(4, byteorder='little'))
            # Number of Spheres
            numberOfSpheres = 0
            numberOfSpheresBinaryPosition = len(binary)
            binary.extend(numberOfSpheres.to_bytes(4, byteorder='little'))  # TODO has to be corrected when the value is known
            # Number of Vertices
            numberOfVertices = len(mesh.vertices)
            binary.extend(numberOfVertices.to_bytes(4, byteorder='little'))
            # Number of Edges
            numberOfEdges = len(mesh.edges)
            binary.extend(numberOfEdges.to_bytes(4, byteorder='little'))
            # Number of Vertex Attributes
            numberOfVertexAttributes = 0  # TODO Vertex Attributes
            binary.extend(numberOfVertexAttributes.to_bytes(4, byteorder='little'))
            # Number of Face Attributes
            numberOfFaceAttributes = 0  # TODO Face Attributes
            binary.extend(numberOfFaceAttributes.to_bytes(4, byteorder='little'))
            # Number of Sphere Attributes
            numberOfSphereAttributes = 0  # TODO Sphere Attributes
            binary.extend(numberOfSphereAttributes.to_bytes(4, byteorder='little'))
            # Vertex data
            vertices = mesh.vertices
            mesh.calc_normals()
            uvCoordinates = numpy.empty(len(vertices), dtype=object)
            if len(mesh.uv_layers) == 0:
                print("Warning: LOD Object: \"%s\" has no uv layers" % (lodObject.name))
                for k in range(len(uvCoordinates)):
                    acos = math.acos(vertices[k].co[1]/(math.sqrt((vertices[k].co[0]*vertices[k].co[0]) + (vertices[k].co[1]*vertices[k].co[1]) + (vertices[k].co[2]*vertices[k].co[2]))))
                    arctan2 = numpy.arctan2(vertices[k].co[2], vertices[k].co[0])
                    uvCoordinates[k] = [acos, arctan2]
            else:
                uv_layer = mesh.uv_layers.active.data
                for polygon in mesh.polygons:
                    for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                        uvCoordinates[mesh.loops[loop_index].vertex_index] = uv_layer[loop_index].uv
            for k in range(len(vertices)):
                vertex = mesh.vertices[k]
                coordinates = vertex.co
                binary.extend(struct.pack('<f', coordinates[0]))
                binary.extend(struct.pack('<f', coordinates[2]))
                binary.extend(struct.pack('<f', coordinates[1]))
                normal = vertex.normal
                binary.extend(struct.pack('<f', normal[0]))
                binary.extend(struct.pack('<f', normal[2]))
                binary.extend(struct.pack('<f', normal[1]))
                # uv
                binary.extend(struct.pack('<f', uvCoordinates[k][0]))
                binary.extend(struct.pack('<f', uvCoordinates[k][1]))
            # Attributes
            # TODO Attributes
            # Triangles
            for polygon in mesh.polygons:
                if len(polygon.vertices) == 3:
                    binary.extend(polygon.vertices[0].to_bytes(4, byteorder='little'))
                    binary.extend(polygon.vertices[1].to_bytes(4, byteorder='little'))
                    binary.extend(polygon.vertices[2].to_bytes(4, byteorder='little'))
            # Quads
            for polygon in mesh.polygons:
                if len(polygon.vertices) == 4:
                    binary.extend(polygon.vertices[0].to_bytes(4, byteorder='little'))
                    binary.extend(polygon.vertices[1].to_bytes(4, byteorder='little'))
                    binary.extend(polygon.vertices[2].to_bytes(4, byteorder='little'))
                    binary.extend(polygon.vertices[3].to_bytes(4, byteorder='little'))
            # Material IDs
            for polygon in mesh.polygons:
                if len(polygon.vertices) == 3:
                    binary.extend(polygon.material_index.to_bytes(2, byteorder='little'))
            for polygon in mesh.polygons:
                if len(polygon.vertices) == 4:
                    binary.extend(polygon.material_index.to_bytes(2, byteorder='little'))
            # Face Attributes
            # TODO Face Attributes
            # Spheres
            # TODO Spheres

            bpy.data.meshes.remove(mesh)
    binFile = open(filepath, 'bw')
    binFile.write(binary)
    binFile.close()
    return 0


def export_mufflon(context, filepath):
    filename = os.path.splitext(filepath)[0]
    binfilepath = filename + ".mff"
    export_json(context, filepath, binfilepath)
    export_binary(context, binfilepath)
    return {'FINISHED'}


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
            default=False,
            )
    path_mode = path_reference_mode

    def execute(self, context):
        return export_mufflon(context, self.filepath)


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
